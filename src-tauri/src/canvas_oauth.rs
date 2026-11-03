#![allow(dead_code)]

use std::fmt;
use std::io::Read;
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::canvas_connection::{
    decode_hex_to_string, encode_hex, CanvasAccessToken, CanvasOrigin,
};
use crate::credential_store::{
    CredentialAvailability, CredentialSlot, CredentialStore, CredentialStoreBackend,
    CredentialStoreError, CredentialStoreFailure, SecretValue,
};
use crate::RuntimeProfile;

pub(crate) const OAUTH_RECORD_PREFIX: &str = "studyhub-canvas-connection-v2";
pub(crate) const OAUTH_CLIENT_PREFIX: &str = "studyhub-canvas-oauth-client-v1";

const OAUTH_AUTH_PATH: &str = "/login/oauth2/auth";
const OAUTH_TOKEN_PATH: &str = "/login/oauth2/token";
const OAUTH_IDENTITY_PATH: &str = "/api/v1/users/self";
const OAUTH_OOB_REDIRECT: &str = "urn:ietf:wg:oauth:2.0:oob";
const OAUTH_STATE_TTL_SECONDS: u64 = 600;
const REFRESH_MARGIN_SECONDS: u64 = 60;
const MAX_CLIENT_ID_BYTES: usize = 512;
const MAX_CLIENT_SECRET_BYTES: usize = 8192;
const MAX_REDIRECT_URI_BYTES: usize = 2048;
const MAX_DISPLAY_NAME_BYTES: usize = 512;
const MAX_TOKEN_RESPONSE_BYTES: usize = 64 * 1024;
const MAX_IDENTITY_RESPONSE_BYTES: usize = 64 * 1024;
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const READ_TIMEOUT: Duration = Duration::from_secs(25);

type NowFn = Arc<dyn Fn() -> u64 + Send + Sync>;

fn system_now_seconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum CanvasConnectionState {
    Unavailable,
    NotConnected,
    Connecting,
    Connected,
    ReconnectRequired,
    Error,
}

impl CanvasConnectionState {
    pub(crate) fn as_str(self) -> &'static str {
        match self {
            Self::Unavailable => "unavailable",
            Self::NotConnected => "not_connected",
            Self::Connecting => "connecting",
            Self::Connected => "connected",
            Self::ReconnectRequired => "reconnect_required",
            Self::Error => "error",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum CanvasOAuthFailure {
    NotProvisioned,
    Cancelled,
    StateMismatch,
    StateExpired,
    StateMissing,
    AccessDenied,
    ExchangeFailed,
    IdentityFailed,
    InvalidOrigin,
    InvalidToken,
    InvalidClientConfig,
    MalformedRecord,
    Missing,
    ReconnectRequired,
    Unauthorized,
    Forbidden,
    RateLimited,
    TransportFailure,
    ServerFailure,
    BackendUnavailable,
    AccessDeniedStore,
    OperationFailed,
    ResponseTooLarge,
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub(crate) struct CanvasOAuthError {
    failure: CanvasOAuthFailure,
}

impl CanvasOAuthError {
    pub(crate) fn new(failure: CanvasOAuthFailure) -> Self {
        Self { failure }
    }

    pub(crate) fn failure(self) -> CanvasOAuthFailure {
        self.failure
    }
}

impl fmt::Debug for CanvasOAuthError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CanvasOAuthError")
            .field("failure", &self.failure)
            .finish()
    }
}

impl fmt::Display for CanvasOAuthError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(error_code(self.failure))
    }
}

impl std::error::Error for CanvasOAuthError {}

fn error_code(failure: CanvasOAuthFailure) -> &'static str {
    match failure {
        CanvasOAuthFailure::NotProvisioned => "oauth_not_provisioned",
        CanvasOAuthFailure::Cancelled => "oauth_cancelled",
        CanvasOAuthFailure::StateMismatch => "oauth_state_mismatch",
        CanvasOAuthFailure::StateExpired => "oauth_state_expired",
        CanvasOAuthFailure::StateMissing => "oauth_state_missing",
        CanvasOAuthFailure::AccessDenied => "oauth_access_denied",
        CanvasOAuthFailure::ExchangeFailed => "oauth_exchange_failed",
        CanvasOAuthFailure::IdentityFailed => "canvas_identity_failed",
        CanvasOAuthFailure::InvalidOrigin => "canvas_invalid_origin",
        CanvasOAuthFailure::InvalidToken => "canvas_invalid_token",
        CanvasOAuthFailure::InvalidClientConfig => "canvas_invalid_oauth_client",
        CanvasOAuthFailure::MalformedRecord => "canvas_malformed_connection_record",
        CanvasOAuthFailure::Missing => "canvas_connection_missing",
        CanvasOAuthFailure::ReconnectRequired => "canvas_reconnect_required",
        CanvasOAuthFailure::Unauthorized => "canvas_unauthorized",
        CanvasOAuthFailure::Forbidden => "canvas_forbidden",
        CanvasOAuthFailure::RateLimited => "canvas_rate_limited",
        CanvasOAuthFailure::TransportFailure => "canvas_transport_failure",
        CanvasOAuthFailure::ServerFailure => "canvas_server_failure",
        CanvasOAuthFailure::BackendUnavailable => "credential_backend_unavailable",
        CanvasOAuthFailure::AccessDeniedStore => "credential_access_denied",
        CanvasOAuthFailure::OperationFailed => "credential_operation_failed",
        CanvasOAuthFailure::ResponseTooLarge => "canvas_response_too_large",
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) enum CanvasRedirect {
    Oob,
    Loopback(String),
}

impl CanvasRedirect {
    fn parse(raw: &str) -> Result<Self, CanvasOAuthError> {
        let value = raw.trim();
        if value.is_empty() || value.len() > MAX_REDIRECT_URI_BYTES {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::InvalidClientConfig,
            ));
        }
        if value == OAUTH_OOB_REDIRECT {
            return Ok(Self::Oob);
        }
        let parsed = url_parse(value)?;
        if parsed.scheme != "http" || !parsed.username.is_empty() || parsed.password.is_some() {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::InvalidClientConfig,
            ));
        }
        let host = parsed
            .host
            .as_deref()
            .map(str::to_ascii_lowercase)
            .unwrap_or_default();
        if host != "127.0.0.1" && host != "localhost" && host != "[::1]" {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::InvalidClientConfig,
            ));
        }
        Ok(Self::Loopback(value.to_string()))
    }

    fn as_str(&self) -> &str {
        match self {
            Self::Oob => OAUTH_OOB_REDIRECT,
            Self::Loopback(value) => value,
        }
    }

    fn needs_code_input(&self) -> bool {
        matches!(self, Self::Oob)
    }
}

struct ParsedUrl {
    scheme: String,
    username: String,
    password: Option<String>,
    host: Option<String>,
    path: String,
    query: Option<String>,
    fragment: Option<String>,
}

fn url_parse(raw: &str) -> Result<ParsedUrl, CanvasOAuthError> {
    let parsed = tauri::Url::parse(raw)
        .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::InvalidClientConfig))?;
    Ok(ParsedUrl {
        scheme: parsed.scheme().to_string(),
        username: parsed.username().to_string(),
        password: parsed.password().map(str::to_string),
        host: parsed.host_str().map(str::to_string),
        path: parsed.path().to_string(),
        query: parsed.query().map(str::to_string),
        fragment: parsed.fragment().map(str::to_string),
    })
}

#[derive(Clone, Eq, PartialEq)]
pub(crate) struct CanvasOAuthClientConfig {
    origin: CanvasOrigin,
    client_id: String,
    client_secret: SecretValue,
    redirect: CanvasRedirect,
}

impl CanvasOAuthClientConfig {
    pub(crate) fn new(
        origin: CanvasOrigin,
        client_id: String,
        client_secret: String,
        redirect: CanvasRedirect,
    ) -> Result<Self, CanvasOAuthError> {
        let client_id = client_id.trim().to_string();
        if client_id.is_empty() || client_id.len() > MAX_CLIENT_ID_BYTES {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::InvalidClientConfig,
            ));
        }
        let client_secret = client_secret.trim().to_string();
        if client_secret.is_empty() || client_secret.len() > MAX_CLIENT_SECRET_BYTES {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::InvalidClientConfig,
            ));
        }
        Ok(Self {
            origin,
            client_id,
            client_secret: SecretValue::new(client_secret),
            redirect,
        })
    }

    pub(crate) fn serialize(&self) -> String {
        format!(
            "{OAUTH_CLIENT_PREFIX}\norigin_hex={}\nclient_id_hex={}\nclient_secret_hex={}\nredirect_uri_hex={}",
            encode_hex(self.origin.as_str().as_bytes()),
            encode_hex(self.client_id.as_bytes()),
            encode_hex(
                self.client_secret
                    .expose_for_trusted_native_use()
                    .as_bytes()
            ),
            encode_hex(self.redirect.as_str().as_bytes()),
        )
    }

    pub(crate) fn parse(serialized: &str) -> Result<Self, CanvasOAuthError> {
        let mut lines = serialized.lines();
        if lines.next() != Some(OAUTH_CLIENT_PREFIX) {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::MalformedRecord,
            ));
        }
        let origin_hex = field(lines.next(), "origin_hex=", 1024)?;
        let origin = decode_hex_to_string(&origin_hex, 512)
            .and_then(|value| CanvasOrigin::parse(&value))
            .map_err(map_connection_error)?;
        let client_id = field(lines.next(), "client_id_hex=", MAX_CLIENT_ID_BYTES * 2)?;
        let client_secret =
            field(lines.next(), "client_secret_hex=", MAX_CLIENT_SECRET_BYTES * 2)?;
        let redirect_raw =
            field(lines.next(), "redirect_uri_hex=", MAX_REDIRECT_URI_BYTES * 2)?;
        if lines.next().is_some() {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::MalformedRecord,
            ));
        }
        let client_id = decode_hex_to_string(&client_id, MAX_CLIENT_ID_BYTES)
            .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::MalformedRecord))?;
        let client_secret = decode_hex_to_string(&client_secret, MAX_CLIENT_SECRET_BYTES)
            .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::MalformedRecord))?;
        let redirect_raw = decode_hex_to_string(&redirect_raw, MAX_REDIRECT_URI_BYTES)
            .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::MalformedRecord))?;
        Self::new(
            origin,
            client_id,
            client_secret,
            CanvasRedirect::parse(&redirect_raw)?,
        )
    }

    pub(crate) fn origin(&self) -> &CanvasOrigin {
        &self.origin
    }
}

impl fmt::Debug for CanvasOAuthClientConfig {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CanvasOAuthClientConfig")
            .field("origin", &self.origin)
            .field("client_id", &self.client_id)
            .field("client_secret", &"<redacted>")
            .field("redirect", &self.redirect)
            .finish()
    }
}

fn field(
    line: Option<&str>,
    prefix: &str,
    max_encoded_bytes: usize,
) -> Result<String, CanvasOAuthError> {
    let value = line
        .and_then(|line| line.strip_prefix(prefix))
        .ok_or_else(|| CanvasOAuthError::new(CanvasOAuthFailure::MalformedRecord))?;
    if value.is_empty() || value.len() > max_encoded_bytes {
        return Err(CanvasOAuthError::new(
            CanvasOAuthFailure::MalformedRecord,
        ));
    }
    Ok(value.to_string())
}

fn map_connection_error(error: crate::canvas_connection::CanvasConnectionError) -> CanvasOAuthError {
    use crate::canvas_connection::CanvasConnectionFailure;
    let failure = match error.failure() {
        CanvasConnectionFailure::InvalidOrigin => CanvasOAuthFailure::InvalidOrigin,
        CanvasConnectionFailure::InvalidToken => CanvasOAuthFailure::InvalidToken,
        CanvasConnectionFailure::MalformedRecord => CanvasOAuthFailure::MalformedRecord,
        CanvasConnectionFailure::Missing => CanvasOAuthFailure::Missing,
        CanvasConnectionFailure::BackendUnavailable => CanvasOAuthFailure::BackendUnavailable,
        CanvasConnectionFailure::AccessDenied => CanvasOAuthFailure::AccessDeniedStore,
        CanvasConnectionFailure::OperationFailed => CanvasOAuthFailure::OperationFailed,
        CanvasConnectionFailure::ManualTokenDevelopmentOnly => CanvasOAuthFailure::AccessDenied,
    };
    CanvasOAuthError::new(failure)
}

#[derive(Clone, Eq, PartialEq)]
pub(crate) struct CanvasOAuthRecord {
    origin: CanvasOrigin,
    access_token: Option<CanvasAccessToken>,
    refresh_token: Option<CanvasAccessToken>,
    expires_at: Option<u64>,
    display_name: String,
    needs_reconnect: bool,
}

impl CanvasOAuthRecord {
    pub(crate) fn new(
        origin: CanvasOrigin,
        access_token: CanvasAccessToken,
        refresh_token: Option<CanvasAccessToken>,
        expires_at: Option<u64>,
        display_name: String,
    ) -> Self {
        Self {
            origin,
            access_token: Some(access_token),
            refresh_token,
            expires_at,
            display_name,
            needs_reconnect: false,
        }
    }

    pub(crate) fn reconnect_required(origin: CanvasOrigin, display_name: String) -> Self {
        Self {
            origin,
            access_token: None,
            refresh_token: None,
            expires_at: None,
            display_name,
            needs_reconnect: true,
        }
    }

    pub(crate) fn serialize(&self) -> String {
        let access = self
            .access_token
            .as_ref()
            .map(|token| token.expose_for_authorized_canvas_request().to_string())
            .unwrap_or_default();
        let refresh = self
            .refresh_token
            .as_ref()
            .map(|token| token.expose_for_authorized_canvas_request().to_string())
            .unwrap_or_default();
        format!(
            "{OAUTH_RECORD_PREFIX}\nauth_mode=oauth\norigin_hex={}\naccess_token_hex={}\nrefresh_token_hex={}\nexpires_at={}\ndisplay_name_hex={}\nneeds_reconnect={}",
            encode_hex(self.origin.as_str().as_bytes()),
            encode_hex(access.as_bytes()),
            encode_hex(refresh.as_bytes()),
            self.expires_at.map(|value| value.to_string()).unwrap_or_default(),
            encode_hex(self.display_name.as_bytes()),
            if self.needs_reconnect { "1" } else { "0" },
        )
    }

    pub(crate) fn parse(serialized: &str) -> Result<Self, CanvasOAuthError> {
        let mut lines = serialized.lines();
        if lines.next() != Some(OAUTH_RECORD_PREFIX) {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::MalformedRecord,
            ));
        }
        if lines.next() != Some("auth_mode=oauth") {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::MalformedRecord,
            ));
        }
        let origin_hex = field(lines.next(), "origin_hex=", 1024)?;
        let access_hex = field(lines.next(), "access_token_hex=", 8192)?;
        let refresh_hex = field(lines.next(), "refresh_token_hex=", 8192)?;
        let expires_raw = field(lines.next(), "expires_at=", 32)?;
        let display_hex = field(lines.next(), "display_name_hex=", MAX_DISPLAY_NAME_BYTES * 2)?;
        let reconnect_raw = field(lines.next(), "needs_reconnect=", 2)?;
        if lines.next().is_some() {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::MalformedRecord,
            ));
        }
        let origin =
            decode_hex_to_string(&origin_hex, 512).and_then(|value| CanvasOrigin::parse(&value))
                .map_err(map_connection_error)?;
        let access = optional_token(&access_hex)?;
        let refresh = optional_token(&refresh_hex)?;
        let expires_at = if expires_raw.is_empty() {
            None
        } else {
            Some(expires_raw.parse::<u64>().map_err(|_| {
                CanvasOAuthError::new(CanvasOAuthFailure::MalformedRecord)
            })?)
        };
        let display_name = decode_hex_to_string(&display_hex, MAX_DISPLAY_NAME_BYTES)
            .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::MalformedRecord))?;
        let needs_reconnect = match reconnect_raw.as_str() {
            "0" => false,
            "1" => true,
            _ => {
                return Err(CanvasOAuthError::new(
                    CanvasOAuthFailure::MalformedRecord,
                ))
            }
        };
        Ok(Self {
            origin,
            access_token: access,
            refresh_token: refresh,
            expires_at,
            display_name,
            needs_reconnect,
        })
    }

    pub(crate) fn origin(&self) -> &CanvasOrigin {
        &self.origin
    }

    pub(crate) fn display_name(&self) -> &str {
        &self.display_name
    }

    pub(crate) fn access_token(&self) -> Option<&CanvasAccessToken> {
        self.access_token.as_ref()
    }

    pub(crate) fn is_expired(&self, now: u64) -> bool {
        match self.expires_at {
            Some(expires_at) => now + REFRESH_MARGIN_SECONDS >= expires_at,
            None => false,
        }
    }
}

impl fmt::Debug for CanvasOAuthRecord {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CanvasOAuthRecord")
            .field("origin", &self.origin)
            .field("access_token", &"<redacted>")
            .field("refresh_token", &"<redacted>")
            .field("expires_at", &self.expires_at)
            .field("display_name", &self.display_name)
            .field("needs_reconnect", &self.needs_reconnect)
            .finish()
    }
}

fn optional_token(encoded: &str) -> Result<Option<CanvasAccessToken>, CanvasOAuthError> {
    if encoded.is_empty() {
        return Ok(None);
    }
    let value = decode_hex_to_string(encoded, 4096)
        .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::MalformedRecord))?;
    CanvasAccessToken::new(value)
        .map(Some)
        .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::MalformedRecord))
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct CanvasStatus {
    pub(crate) state: CanvasConnectionState,
    pub(crate) auth_mode: Option<&'static str>,
    pub(crate) origin: Option<String>,
    pub(crate) display_name: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct CanvasOAuthStart {
    pub(crate) auth_url: String,
    pub(crate) needs_code_input: bool,
}

#[derive(Clone)]
struct PendingOAuthState {
    state: String,
    created_at: u64,
}

pub(crate) struct CanvasCredentialManager<S: CredentialStore> {
    profile: RuntimeProfile,
    store: S,
    now: NowFn,
    refresh_lock: Mutex<()>,
    pending_oauth: Mutex<Option<PendingOAuthState>>,
}

impl CanvasCredentialManager<CredentialStoreBackend> {
    pub(crate) fn for_profile(profile: RuntimeProfile) -> Self {
        Self::new(
            profile,
            crate::credential_store::credential_store_for_profile(profile),
            Arc::new(system_now_seconds),
        )
    }
}

impl<S: CredentialStore> CanvasCredentialManager<S> {
    pub(crate) fn new(profile: RuntimeProfile, store: S, now: NowFn) -> Self {
        Self {
            profile,
            store,
            now,
            refresh_lock: Mutex::new(()),
            pending_oauth: Mutex::new(None),
        }
    }

    pub(crate) fn provision_client_config(
        &self,
        origin: &str,
        client_id: &str,
        client_secret: &str,
        redirect_uri: &str,
    ) -> Result<(), CanvasOAuthError> {
        let config = CanvasOAuthClientConfig::new(
            CanvasOrigin::parse(origin).map_err(map_connection_error)?,
            client_id.to_string(),
            client_secret.to_string(),
            CanvasRedirect::parse(redirect_uri)?,
        )?;
        self.store
            .store_replace(
                CredentialSlot::CanvasOAuthClient,
                &SecretValue::new(config.serialize()),
            )
            .map_err(map_store_error)
    }

    pub(crate) fn load_client_config(
        &self,
    ) -> Result<CanvasOAuthClientConfig, CanvasOAuthError> {
        match self.store.get_for_trusted_native_use(CredentialSlot::CanvasOAuthClient) {
            Ok(secret) => CanvasOAuthClientConfig::parse(secret.expose_for_trusted_native_use()),
            Err(error) if error.failure() == CredentialStoreFailure::Missing => {
                Err(CanvasOAuthError::new(CanvasOAuthFailure::NotProvisioned))
            }
            Err(error) => Err(map_store_error(error)),
        }
    }

    pub(crate) fn status(&self) -> CanvasStatus {
        let oauth_record = self.load_record(CredentialSlot::CanvasOAuth);
        if let Ok(record) = oauth_record {
            if record.needs_reconnect {
                return CanvasStatus {
                    state: CanvasConnectionState::ReconnectRequired,
                    auth_mode: Some("oauth"),
                    origin: Some(record.origin.as_str().to_string()),
                    display_name: nonempty(record.display_name()),
                };
            }
            if record.access_token().is_some() && !record.is_expired((self.now)()) {
                return CanvasStatus {
                    state: CanvasConnectionState::Connected,
                    auth_mode: Some("oauth"),
                    origin: Some(record.origin.as_str().to_string()),
                    display_name: nonempty(record.display_name()),
                };
            }
            return CanvasStatus {
                state: CanvasConnectionState::ReconnectRequired,
                auth_mode: Some("oauth"),
                origin: Some(record.origin.as_str().to_string()),
                display_name: nonempty(record.display_name()),
            };
        }

        if self.profile == RuntimeProfile::Development {
            match self.store.get_for_trusted_native_use(CredentialSlot::CanvasDefault) {
                Ok(secret) => {
                    if let Ok(record) = crate::canvas_connection::CanvasConnectionRecord::parse(
                        secret.expose_for_trusted_native_use(),
                    ) {
                        return CanvasStatus {
                            state: CanvasConnectionState::Connected,
                            auth_mode: Some("pat"),
                            origin: Some(record.origin().as_str().to_string()),
                            display_name: None,
                        };
                    }
                }
                Err(_) => {}
            }
        }

        match self.store.exists(CredentialSlot::CanvasOAuthClient) {
            Ok(CredentialAvailability::Configured) => CanvasStatus {
                state: CanvasConnectionState::NotConnected,
                auth_mode: None,
                origin: self
                    .load_client_config()
                    .ok()
                    .map(|config| config.origin().as_str().to_string()),
                display_name: None,
            },
            Ok(CredentialAvailability::Missing) => CanvasStatus {
                state: CanvasConnectionState::Unavailable,
                auth_mode: None,
                origin: None,
                display_name: None,
            },
            Err(_) => CanvasStatus {
                state: CanvasConnectionState::Error,
                auth_mode: None,
                origin: None,
                display_name: None,
            },
        }
    }

    pub(crate) fn begin_oauth(&self) -> Result<CanvasOAuthStart, CanvasOAuthError> {
        let config = self.load_client_config()?;
        let state = generate_state()?;
        let auth_url = build_authorization_url(&config, &state)?;
        *self.pending_oauth.lock().expect("oauth lock") = Some(PendingOAuthState {
            state,
            created_at: (self.now)(),
        });
        Ok(CanvasOAuthStart {
            auth_url,
            needs_code_input: config.redirect.needs_code_input(),
        })
    }

    pub(crate) fn cancel_oauth(&self) {
        *self.pending_oauth.lock().expect("oauth lock") = None;
    }

    pub(crate) fn complete_oauth(&self, code: &str) -> Result<CanvasStatus, CanvasOAuthError> {
        let config = self.load_client_config()?;
        let pending = self.take_pending_state()?;
        self.complete_with_pending(&config, &pending.state, code)
    }

    pub(crate) fn complete_oauth_with_state(
        &self,
        code: &str,
        state: &str,
    ) -> Result<CanvasStatus, CanvasOAuthError> {
        let config = self.load_client_config()?;
        let pending = self.take_pending_state()?;
        if state.is_empty() || pending.state != state {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::StateMismatch,
            ));
        }
        self.complete_with_pending(&config, &pending.state, code)
    }

    fn take_pending_state(&self) -> Result<PendingOAuthState, CanvasOAuthError> {
        let mut guard = self.pending_oauth.lock().expect("oauth lock");
        let pending = guard
            .take()
            .ok_or_else(|| CanvasOAuthError::new(CanvasOAuthFailure::StateMissing))?;
        if (self.now)() >= pending.created_at.saturating_add(OAUTH_STATE_TTL_SECONDS) {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::StateExpired,
            ));
        }
        Ok(pending)
    }

    fn complete_with_pending(
        &self,
        config: &CanvasOAuthClientConfig,
        _state: &str,
        code: &str,
    ) -> Result<CanvasStatus, CanvasOAuthError> {
        let code = code.trim();
        if code.is_empty() || code.len() > 4096 {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::ExchangeFailed,
            ));
        }
        let token_payload = exchange_authorization_code(config, code)?;
        let access_token = CanvasAccessToken::new(token_payload.access_token)
            .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::ExchangeFailed))?;
        let refresh_token = token_payload
            .refresh_token
            .map(|value| CanvasAccessToken::new(value))
            .transpose()
            .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::ExchangeFailed))?;
        let display_name = validate_identity(config.origin(), &access_token)?;
        let expires_at = token_payload.expires_in.map(|seconds| {
            (self.now)().saturating_add(seconds.max(1))
        });
        let record = CanvasOAuthRecord::new(
            config.origin().clone(),
            access_token.clone(),
            refresh_token,
            expires_at,
            display_name.clone(),
        );
        self.persist_connected(&record, &access_token)?;
        Ok(CanvasStatus {
            state: CanvasConnectionState::Connected,
            auth_mode: Some("oauth"),
            origin: Some(config.origin().as_str().to_string()),
            display_name: nonempty(&display_name),
        })
    }

    fn persist_connected(
        &self,
        record: &CanvasOAuthRecord,
        access_token: &CanvasAccessToken,
    ) -> Result<(), CanvasOAuthError> {
        self.store
            .store_replace(
                CredentialSlot::CanvasOAuth,
                &SecretValue::new(record.serialize()),
            )
            .map_err(map_store_error)?;
        let projection = crate::canvas_connection::CanvasConnectionRecord::new(
            record.origin().clone(),
            access_token.clone(),
        );
        self.store
            .store_replace(
                CredentialSlot::CanvasDefault,
                &SecretValue::new(projection.serialize()),
            )
            .map_err(map_store_error)
    }

    pub(crate) fn resolve_canvas_default(&self) -> Result<SecretValue, CanvasOAuthError> {
        if let Ok(record) = self.load_record(CredentialSlot::CanvasOAuth) {
            if record.needs_reconnect {
                return Err(CanvasOAuthError::new(
                    CanvasOAuthFailure::ReconnectRequired,
                ));
            }
            let now = (self.now)();
            if record.access_token().is_some() && record.is_expired(now) {
                let _guard = self.refresh_lock.lock().expect("refresh lock");
                return self.refresh_and_project();
            }
            if let Some(access_token) = record.access_token() {
                let projection = crate::canvas_connection::CanvasConnectionRecord::new(
                    record.origin().clone(),
                    access_token.clone(),
                );
                return Ok(SecretValue::new(projection.serialize()));
            }
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::ReconnectRequired,
            ));
        }
        self.store
            .get_for_trusted_native_use(CredentialSlot::CanvasDefault)
            .map_err(|error| {
                if error.failure() == CredentialStoreFailure::Missing {
                    CanvasOAuthError::new(CanvasOAuthFailure::Missing)
                } else {
                    map_store_error(error)
                }
            })
    }

    fn refresh_and_project(&self) -> Result<SecretValue, CanvasOAuthError> {
        let config = self.load_client_config()?;
        let record = self.load_record(CredentialSlot::CanvasOAuth)?;
        let refresh_token = record
            .refresh_token
            .as_ref()
            .ok_or_else(|| CanvasOAuthError::new(CanvasOAuthFailure::ReconnectRequired))?;
        match refresh_access_token(&config, refresh_token) {
            Ok(payload) => {
                let access_token = CanvasAccessToken::new(payload.access_token)
                    .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::ExchangeFailed))?;
                let new_refresh_token = payload
                    .refresh_token
                    .map(|value| CanvasAccessToken::new(value))
                    .transpose()
                    .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::ExchangeFailed))?
                    .or_else(|| record.refresh_token.clone());
                let expires_at = payload.expires_in.map(|seconds| {
                    (self.now)().saturating_add(seconds.max(1))
                });
                let updated = CanvasOAuthRecord {
                    origin: record.origin().clone(),
                    access_token: Some(access_token.clone()),
                    refresh_token: new_refresh_token,
                    expires_at,
                    display_name: record.display_name().to_string(),
                    needs_reconnect: false,
                };
                self.persist_connected(&updated, &access_token)?;
                let projection = crate::canvas_connection::CanvasConnectionRecord::new(
                    record.origin().clone(),
                    access_token,
                );
                Ok(SecretValue::new(projection.serialize()))
            }
            Err(error) if is_definitive_refresh_failure(error.failure()) => {
                let reconnect = CanvasOAuthRecord::reconnect_required(
                    record.origin().clone(),
                    record.display_name().to_string(),
                );
                self.store
                    .store_replace(
                        CredentialSlot::CanvasOAuth,
                        &SecretValue::new(reconnect.serialize()),
                    )
                    .map_err(map_store_error)?;
                self.store
                    .delete(CredentialSlot::CanvasDefault)
                    .map_err(map_store_error)?;
                Err(CanvasOAuthError::new(
                    CanvasOAuthFailure::ReconnectRequired,
                ))
            }
            Err(error) => Err(error),
        }
    }

    pub(crate) fn disconnect(
        &self,
        revoke: bool,
    ) -> Result<bool, CanvasOAuthError> {
        let mut revoked = false;
        if revoke {
            revoked = self.revoke_remote()?;
        }
        for slot in [CredentialSlot::CanvasDefault, CredentialSlot::CanvasOAuth] {
            match self.store.delete(slot) {
                Ok(()) => {}
                Err(error) if error.failure() == CredentialStoreFailure::Missing => {}
                Err(error) => return Err(map_store_error(error)),
            }
        }
        self.cancel_oauth();
        Ok(revoked)
    }

    fn revoke_remote(&self) -> Result<bool, CanvasOAuthError> {
        let config = match self.load_client_config() {
            Ok(config) => config,
            Err(_) => return Ok(false),
        };
        let record = match self.load_record(CredentialSlot::CanvasOAuth) {
            Ok(record) => record,
            Err(_) => return Ok(false),
        };
        let Some(access_token) = record.access_token() else {
            return Ok(false);
        };
        let client_secret = config
            .client_secret
            .expose_for_trusted_native_use()
            .to_string();
        post_token_form(
            &format!("{}/login/oauth2/token", config.origin().as_str()),
            &[
                ("token", access_token.expose_for_authorized_canvas_request()),
                ("client_id", &config.client_id),
                ("client_secret", &client_secret),
            ],
        )
        .map(|_| true)
        .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::ServerFailure))
    }

    fn load_record(
        &self,
        slot: CredentialSlot,
    ) -> Result<CanvasOAuthRecord, CanvasOAuthError> {
        match self.store.get_for_trusted_native_use(slot) {
            Ok(secret) => CanvasOAuthRecord::parse(secret.expose_for_trusted_native_use()),
            Err(error) if error.failure() == CredentialStoreFailure::Missing => {
                Err(CanvasOAuthError::new(CanvasOAuthFailure::Missing))
            }
            Err(error) => Err(map_store_error(error)),
        }
    }
}

fn nonempty(value: &str) -> Option<String> {
    if value.is_empty() {
        None
    } else {
        Some(value.to_string())
    }
}

fn generate_state() -> Result<String, CanvasOAuthError> {
    #[cfg(unix)]
    {
        let mut bytes = [0u8; 32];
        std::fs::File::open("/dev/urandom")
            .and_then(|mut source| source.read_exact(&mut bytes))
            .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::OperationFailed))?;
        return Ok(encode_hex(&bytes));
    }
    #[cfg(not(unix))]
    Err(CanvasOAuthError::new(
        CanvasOAuthFailure::OperationFailed,
    ))
}

fn build_authorization_url(
    config: &CanvasOAuthClientConfig,
    state: &str,
) -> Result<String, CanvasOAuthError> {
    let mut url = String::from(config.origin().as_str());
    url.push_str(OAUTH_AUTH_PATH);
    url.push_str("?client_id=");
    url.push_str(&urlencode(&config.client_id));
    url.push_str("&response_type=code");
    url.push_str("&state=");
    url.push_str(&urlencode(state));
    url.push_str("&redirect_uri=");
    url.push_str(&urlencode(config.redirect.as_str()));
    if url.len() > 8192 {
        return Err(CanvasOAuthError::new(
            CanvasOAuthFailure::InvalidClientConfig,
        ));
    }
    Ok(url)
}

fn urlencode(value: &str) -> String {
    let mut output = String::with_capacity(value.len());
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => {
                output.push(byte as char)
            }
            _ => output.push_str(&format!("%{byte:02X}")),
        }
    }
    output
}

struct TokenPayload {
    access_token: String,
    refresh_token: Option<String>,
    expires_in: Option<u64>,
}

fn exchange_authorization_code(
    config: &CanvasOAuthClientConfig,
    code: &str,
) -> Result<TokenPayload, CanvasOAuthError> {
    let token_url = format!("{}{}", config.origin().as_str(), OAUTH_TOKEN_PATH);
    let client_secret = config
        .client_secret
        .expose_for_trusted_native_use()
        .to_string();
    let body = post_token_form(
        &token_url,
        &[
            ("grant_type", "authorization_code"),
            ("client_id", &config.client_id),
            ("client_secret", &client_secret),
            ("redirect_uri", config.redirect.as_str()),
            ("code", code),
        ],
    )?;
    parse_token_payload(&body)
}

fn refresh_access_token(
    config: &CanvasOAuthClientConfig,
    refresh_token: &CanvasAccessToken,
) -> Result<TokenPayload, CanvasOAuthError> {
    let token_url = format!("{}{}", config.origin().as_str(), OAUTH_TOKEN_PATH);
    let client_secret = config
        .client_secret
        .expose_for_trusted_native_use()
        .to_string();
    let body = post_token_form(
        &token_url,
        &[
            ("grant_type", "refresh_token"),
            ("client_id", &config.client_id),
            ("client_secret", &client_secret),
            (
                "refresh_token",
                refresh_token.expose_for_authorized_canvas_request(),
            ),
        ],
    )?;
    parse_token_payload(&body)
}

fn post_token_form(
    url: &str,
    fields: &[(&str, &str)],
) -> Result<String, CanvasOAuthError> {
    let agent = oauth_agent();
    let response = agent
        .post(url)
        .set("Accept", "application/json")
        .send_form(fields);
    match response {
        Ok(response) => read_bounded_body(response),
        Err(ureq::Error::Status(status, response)) => {
            let failure = match status {
                401 => CanvasOAuthFailure::Unauthorized,
                403 => CanvasOAuthFailure::Forbidden,
                429 => CanvasOAuthFailure::RateLimited,
                value if value >= 500 => CanvasOAuthFailure::ServerFailure,
                _ => CanvasOAuthFailure::ExchangeFailed,
            };
            let _ = read_bounded_body(response);
            Err(CanvasOAuthError::new(failure))
        }
        Err(ureq::Error::Transport(_)) => Err(CanvasOAuthError::new(
            CanvasOAuthFailure::TransportFailure,
        )),
    }
}

fn oauth_agent() -> ureq::Agent {
    ureq::AgentBuilder::new()
        .timeout_connect(CONNECT_TIMEOUT)
        .timeout_read(READ_TIMEOUT)
        .timeout_write(READ_TIMEOUT)
        .redirects(0)
        .build()
}

fn read_bounded_body(response: ureq::Response) -> Result<String, CanvasOAuthError> {
    let mut reader = response.into_reader();
    let mut buffer = Vec::new();
    let mut chunk = [0u8; 8192];
    loop {
        let count = reader
            .read(&mut chunk)
            .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::TransportFailure))?;
        if count == 0 {
            break;
        }
        buffer.extend_from_slice(&chunk[..count]);
        if buffer.len() > MAX_TOKEN_RESPONSE_BYTES {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::ResponseTooLarge,
            ));
        }
    }
    String::from_utf8(buffer)
        .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::ExchangeFailed))
}

fn parse_token_payload(body: &str) -> Result<TokenPayload, CanvasOAuthError> {
    let payload: serde_json::Value = serde_json::from_str(body)
        .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::ExchangeFailed))?;
    let access_token = payload
        .get("access_token")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| CanvasOAuthError::new(CanvasOAuthFailure::ExchangeFailed))?
        .to_string();
    if access_token.trim().is_empty() || access_token.len() > 4096 {
        return Err(CanvasOAuthError::new(
            CanvasOAuthFailure::ExchangeFailed,
        ));
    }
    let refresh_token = payload
        .get("refresh_token")
        .and_then(serde_json::Value::as_str)
        .filter(|value| !value.is_empty() && value.len() <= 4096)
        .map(str::to_string);
    let expires_in = payload
        .get("expires_in")
        .and_then(serde_json::Value::as_u64)
        .filter(|value| *value > 0);
    Ok(TokenPayload {
        access_token,
        refresh_token,
        expires_in,
    })
}

fn validate_identity(
    origin: &CanvasOrigin,
    access_token: &CanvasAccessToken,
) -> Result<String, CanvasOAuthError> {
    let url = format!("{}{}", origin.as_str(), OAUTH_IDENTITY_PATH);
    let agent = oauth_agent();
    let response = agent
        .get(&url)
        .set("Accept", "application/json")
        .set(
            "Authorization",
            &format!(
                "Bearer {}",
                access_token.expose_for_authorized_canvas_request()
            ),
        )
        .call();
    match response {
        Ok(response) => {
            let body = read_identity_body(response)?;
            let payload: serde_json::Value = serde_json::from_str(&body)
                .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::IdentityFailed))?;
            let display_name = payload
                .get("name")
                .or_else(|| payload.get("short_name"))
                .and_then(serde_json::Value::as_str)
                .unwrap_or("")
                .to_string();
            if payload.get("id").is_none() {
                return Err(CanvasOAuthError::new(
                    CanvasOAuthFailure::IdentityFailed,
                ));
            }
            Ok(display_name)
        }
        Err(ureq::Error::Status(status, response)) => {
            let _ = read_bounded_body(response);
            let failure = match status {
                401 => CanvasOAuthFailure::Unauthorized,
                403 => CanvasOAuthFailure::Forbidden,
                429 => CanvasOAuthFailure::RateLimited,
                value if value >= 500 => CanvasOAuthFailure::ServerFailure,
                _ => CanvasOAuthFailure::IdentityFailed,
            };
            Err(CanvasOAuthError::new(failure))
        }
        Err(ureq::Error::Transport(_)) => Err(CanvasOAuthError::new(
            CanvasOAuthFailure::TransportFailure,
        )),
    }
}

fn read_identity_body(response: ureq::Response) -> Result<String, CanvasOAuthError> {
    let mut reader = response.into_reader();
    let mut buffer = Vec::new();
    let mut chunk = [0u8; 8192];
    loop {
        let count = reader
            .read(&mut chunk)
            .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::TransportFailure))?;
        if count == 0 {
            break;
        }
        buffer.extend_from_slice(&chunk[..count]);
        if buffer.len() > MAX_IDENTITY_RESPONSE_BYTES {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::ResponseTooLarge,
            ));
        }
    }
    String::from_utf8(buffer)
        .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::IdentityFailed))
}

fn is_definitive_refresh_failure(failure: CanvasOAuthFailure) -> bool {
    matches!(
        failure,
        CanvasOAuthFailure::Unauthorized
            | CanvasOAuthFailure::Forbidden
            | CanvasOAuthFailure::ExchangeFailed
    )
}

fn map_store_error(error: CredentialStoreError) -> CanvasOAuthError {
    let failure = match error.failure() {
        CredentialStoreFailure::Missing => CanvasOAuthFailure::Missing,
        CredentialStoreFailure::BackendUnavailable => CanvasOAuthFailure::BackendUnavailable,
        CredentialStoreFailure::AccessDenied => CanvasOAuthFailure::AccessDeniedStore,
        CredentialStoreFailure::OperationFailed => CanvasOAuthFailure::OperationFailed,
    };
    CanvasOAuthError::new(failure)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::credential_store::{
        namespace_for_profile, MemoryCredentialStore,
    };
    use std::sync::atomic::{AtomicU64, Ordering};

    fn manager(profile: RuntimeProfile) -> (
        CanvasCredentialManager<MemoryCredentialStore>,
        Arc<AtomicU64>,
    ) {
        let clock = Arc::new(AtomicU64::new(1_700_000_000));
        let now = {
            let clock = clock.clone();
            Arc::new(move || clock.load(Ordering::SeqCst)) as NowFn
        };
        let store = MemoryCredentialStore::new(namespace_for_profile(profile));
        (
            CanvasCredentialManager::new(profile, store, now),
            clock,
        )
    }

    fn provision(manager: &CanvasCredentialManager<MemoryCredentialStore>) {
        manager
            .provision_client_config(
                "https://canvas.example.edu",
                "synthetic-client-id",
                "synthetic-client-secret",
                "urn:ietf:wg:oauth:2.0:oob",
            )
            .expect("provision synthetic client");
    }

    #[test]
    fn oauth_client_config_round_trips_and_redacts_secret() {
        let config = CanvasOAuthClientConfig::new(
            CanvasOrigin::parse("https://canvas.example.edu").expect("origin"),
            "synthetic-client-id".to_string(),
            "synthetic-client-secret".to_string(),
            CanvasRedirect::parse("urn:ietf:wg:oauth:2.0:oob").expect("redirect"),
        )
        .expect("config");
        let serialized = config.serialize();
        assert!(serialized.starts_with(OAUTH_CLIENT_PREFIX));
        assert!(!format!("{config:?}").contains("synthetic-client-secret"));
        let parsed = CanvasOAuthClientConfig::parse(&serialized).expect("parse");
        assert_eq!(parsed.origin().as_str(), "https://canvas.example.edu");
    }

    #[test]
    fn oauth_record_round_trips_and_redacts_tokens() {
        let record = CanvasOAuthRecord::new(
            CanvasOrigin::parse("https://canvas.example.edu").expect("origin"),
            CanvasAccessToken::new("synthetic-access").expect("access"),
            Some(CanvasAccessToken::new("synthetic-refresh").expect("refresh")),
            Some(1_700_000_300),
            "Synthetic Student".to_string(),
        );
        let serialized = record.serialize();
        assert!(serialized.starts_with(OAUTH_RECORD_PREFIX));
        assert!(!format!("{record:?}").contains("synthetic-access"));
        assert!(!format!("{record:?}").contains("synthetic-refresh"));
        let parsed = CanvasOAuthRecord::parse(&serialized).expect("parse");
        assert_eq!(parsed.origin().as_str(), "https://canvas.example.edu");
        assert_eq!(parsed.display_name(), "Synthetic Student");
    }

    #[test]
    fn state_missing_mismatch_and_consumed_once() {
        let (manager, _clock) = manager(RuntimeProfile::Development);
        provision(&manager);
        assert_eq!(
            manager
                .complete_oauth("synthetic-code")
                .unwrap_err()
                .failure(),
            CanvasOAuthFailure::StateMissing
        );
        let start = manager.begin_oauth().expect("begin");
        assert!(start.needs_code_input);
        assert!(start.auth_url.contains("state="));
        assert_eq!(
            manager
                .complete_oauth_with_state("synthetic-code", "wrong-state")
                .unwrap_err()
                .failure(),
            CanvasOAuthFailure::StateMismatch
        );
        assert_eq!(
            manager
                .complete_oauth_with_state("synthetic-code", "wrong-state")
                .unwrap_err()
                .failure(),
            CanvasOAuthFailure::StateMissing
        );
    }

    #[test]
    fn unavailable_and_not_connected_states_are_derived() {
        let (manager, _clock) = manager(RuntimeProfile::Production);
        assert_eq!(
            manager.status().state,
            CanvasConnectionState::Unavailable
        );
        provision(&manager);
        assert_eq!(
            manager.status().state,
            CanvasConnectionState::NotConnected
        );
    }

    #[test]
    fn development_pat_record_is_reported_as_connected_pat() {
        let (manager, _clock) = manager(RuntimeProfile::Development);
        crate::canvas_connection::configure_canvas_development_connection_in_store(
            RuntimeProfile::Development,
            &manager.store,
            "https://canvas.example.edu",
            "synthetic-pat",
        )
        .expect("configure dev PAT");
        let status = manager.status();
        assert_eq!(status.state, CanvasConnectionState::Connected);
        assert_eq!(status.auth_mode, Some("pat"));
        assert_eq!(manager.resolve_canvas_default().is_ok(), true);
    }

    #[test]
    fn production_never_accepts_development_pat() {
        let (manager, _clock) = manager(RuntimeProfile::Production);
        let error = crate::canvas_connection::configure_canvas_development_connection_in_store(
            RuntimeProfile::Production,
            &manager.store,
            "https://canvas.example.edu",
            "synthetic-pat",
        )
        .unwrap_err();
        assert!(!error.to_string().contains("synthetic-pat"));
        assert_eq!(
            manager.status().state,
            CanvasConnectionState::Unavailable
        );
    }

    #[test]
    fn oauth_record_projection_stays_out_of_default_slot() {
        let (manager, _clock) = manager(RuntimeProfile::Development);
        provision(&manager);
        let record = CanvasOAuthRecord::new(
            CanvasOrigin::parse("https://canvas.example.edu").expect("origin"),
            CanvasAccessToken::new("synthetic-access").expect("access"),
            Some(CanvasAccessToken::new("synthetic-refresh").expect("refresh")),
            Some(1_700_000_300),
            "Synthetic Student".to_string(),
        );
        manager
            .store
            .store_replace(
                CredentialSlot::CanvasOAuth,
                &SecretValue::new(record.serialize()),
            )
            .expect("store oauth");
        let default = manager
            .store
            .get_for_trusted_native_use(CredentialSlot::CanvasDefault);
        assert!(default.is_err());
    }

    #[test]
    fn disconnect_preserves_oauth_client_config() {
        let (manager, _clock) = manager(RuntimeProfile::Development);
        provision(&manager);
        let record = CanvasOAuthRecord::new(
            CanvasOrigin::parse("https://canvas.example.edu").expect("origin"),
            CanvasAccessToken::new("synthetic-access").expect("access"),
            Some(CanvasAccessToken::new("synthetic-refresh").expect("refresh")),
            Some(1_700_000_300),
            "Synthetic Student".to_string(),
        );
        manager
            .persist_connected(
                &record,
                record.access_token().expect("access"),
            )
            .expect("persist");
        manager.disconnect(false).expect("disconnect");
        assert_eq!(
            manager.status().state,
            CanvasConnectionState::NotConnected
        );
    }
}
