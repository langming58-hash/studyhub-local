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
pub(crate) const OAUTH_AUTH_PATH: &str = "/login/oauth2/auth";
pub(crate) const OAUTH_TOKEN_PATH: &str = "/login/oauth2/token";
pub(crate) const OAUTH_IDENTITY_PATH: &str = "/api/v1/users/self";
pub(crate) const OAUTH_RESPONSE_PATH: &str = "/login/oauth2/auth";
pub(crate) const OAUTH_OOB_REDIRECT: &str = "urn:ietf:wg:oauth:2.0:oob";

pub(crate) const CANVAS_READ_SCOPES: [&str; 4] = [
    "url:GET|/api/v1/users/:id",
    "url:GET|/api/v1/courses",
    "url:GET|/api/v1/courses/:course_id/files",
    "url:GET|/api/v1/courses/:course_id/files/:id",
];

const OAUTH_STATE_TTL_SECONDS: u64 = 600;
const REFRESH_MARGIN_SECONDS: u64 = 60;
const MAX_CLIENT_ID_BYTES: usize = 512;
const MAX_CLIENT_SECRET_BYTES: usize = 8192;
const MAX_REDIRECT_URI_BYTES: usize = 2048;
const MAX_DISPLAY_NAME_BYTES: usize = 512;
const MAX_STATE_BYTES: usize = 256;
const MAX_CODE_BYTES: usize = 4096;
const MAX_TOKEN_BYTES: usize = 4096;
const MAX_RESPONSE_URL_BYTES: usize = 8192;
const MAX_HTTP_BODY_BYTES: usize = 64 * 1024;
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
    ClientBindingMismatch,
    MalformedRecord,
    MalformedResponse,
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
        CanvasOAuthFailure::ClientBindingMismatch => "oauth_client_binding_mismatch",
        CanvasOAuthFailure::MalformedRecord => "canvas_malformed_connection_record",
        CanvasOAuthFailure::MalformedResponse => "canvas_malformed_oauth_response",
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

#[derive(Clone, Eq, PartialEq)]
pub(crate) struct CanvasOAuthRequest {
    pub(crate) method: &'static str,
    pub(crate) url: String,
    pub(crate) headers: Vec<(String, String)>,
    pub(crate) form: Vec<(String, String)>,
}

impl fmt::Debug for CanvasOAuthRequest {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        const SECRET_HEADERS: &[&str] = &["authorization", "proxy-authorization", "cookie"];
        const SECRET_FORM_FIELDS: &[&str] = &[
            "client_secret",
            "refresh_token",
            "access_token",
            "code",
            "token",
        ];
        let mut debug = formatter.debug_struct("CanvasOAuthRequest");
        debug.field("method", &self.method);
        debug.field("url", &self.url);
        let headers: Vec<String> = self
            .headers
            .iter()
            .map(|(name, value)| {
                if SECRET_HEADERS.contains(&name.to_ascii_lowercase().as_str()) {
                    format!("{name}: <redacted>")
                } else {
                    format!("{name}: {value}")
                }
            })
            .collect();
        debug.field("headers", &headers);
        let form_fields: Vec<String> = self
            .form
            .iter()
            .map(|(name, value)| {
                if SECRET_FORM_FIELDS.contains(&name.as_str()) {
                    format!("{name}=<redacted>")
                } else {
                    format!("{name}={value}")
                }
            })
            .collect();
        debug.field("form_fields", &form_fields);
        debug.finish()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct CanvasOAuthHttpResponse {
    pub(crate) status: u16,
    pub(crate) body: String,
}

pub(crate) trait CanvasOAuthHttp: Send + Sync {
    fn send(&self, request: CanvasOAuthRequest) -> Result<CanvasOAuthHttpResponse, CanvasOAuthError>;
}

pub(crate) struct UreqCanvasOAuthHttp;

impl CanvasOAuthHttp for UreqCanvasOAuthHttp {
    fn send(&self, request: CanvasOAuthRequest) -> Result<CanvasOAuthHttpResponse, CanvasOAuthError> {
        let agent = oauth_agent();
        let response = match request.method {
            "POST" => {
                let mut builder = agent.post(&request.url).set("Accept", "application/json");
                for (name, value) in &request.headers {
                    builder = builder.set(name.as_str(), value.as_str());
                }
                let form: Vec<(&str, &str)> = request
                    .form
                    .iter()
                    .map(|(name, value)| (name.as_str(), value.as_str()))
                    .collect();
                builder.send_form(&form)
            }
            "GET" => {
                let mut builder = agent.get(&request.url).set("Accept", "application/json");
                for (name, value) in &request.headers {
                    builder = builder.set(name.as_str(), value.as_str());
                }
                builder.call()
            }
            "DELETE" => {
                let mut builder = agent.delete(&request.url);
                for (name, value) in &request.headers {
                    builder = builder.set(name.as_str(), value.as_str());
                }
                builder.call()
            }
            _ => return Err(CanvasOAuthError::new(CanvasOAuthFailure::ExchangeFailed)),
        };
        match response {
            Ok(response) => {
                let status = response.status();
                let body = read_bounded_body(response)?;
                Ok(CanvasOAuthHttpResponse { status, body })
            }
            Err(ureq::Error::Status(status, response)) => {
                let body = read_bounded_body(response)?;
                Ok(CanvasOAuthHttpResponse { status, body })
            }
            Err(ureq::Error::Transport(_)) => Err(CanvasOAuthError::new(
                CanvasOAuthFailure::TransportFailure,
            )),
        }
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
        if buffer.len() > MAX_HTTP_BODY_BYTES {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::ResponseTooLarge,
            ));
        }
    }
    String::from_utf8(buffer)
        .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::ExchangeFailed))
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct CanvasRedirect(String);

impl CanvasRedirect {
    fn parse(raw: &str) -> Result<Self, CanvasOAuthError> {
        let value = raw.trim();
        if value != OAUTH_OOB_REDIRECT || value.len() > MAX_REDIRECT_URI_BYTES {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::InvalidClientConfig,
            ));
        }
        Ok(Self(value.to_string()))
    }

    pub(crate) fn as_str(&self) -> &str {
        &self.0
    }
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
        let origin_hex = required_field(lines.next(), "origin_hex=", 1024)?;
        let client_id_hex = required_field(lines.next(), "client_id_hex=", MAX_CLIENT_ID_BYTES * 2)?;
        let client_secret_hex =
            required_field(lines.next(), "client_secret_hex=", MAX_CLIENT_SECRET_BYTES * 2)?;
        let redirect_hex =
            required_field(lines.next(), "redirect_uri_hex=", MAX_REDIRECT_URI_BYTES * 2)?;
        if lines.next().is_some() {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::MalformedRecord,
            ));
        }
        let origin = decode_hex_to_string(&origin_hex, 512)
            .and_then(|value| CanvasOrigin::parse(&value))
            .map_err(map_connection_error)?;
        let client_id = decode_hex_to_string(&client_id_hex, MAX_CLIENT_ID_BYTES)
            .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::MalformedRecord))?;
        let client_secret = decode_hex_to_string(&client_secret_hex, MAX_CLIENT_SECRET_BYTES)
            .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::MalformedRecord))?;
        let redirect = decode_hex_to_string(&redirect_hex, MAX_REDIRECT_URI_BYTES)
            .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::MalformedRecord))?;
        Self::new(origin, client_id, client_secret, CanvasRedirect::parse(&redirect)?)
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

fn required_field(
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

fn optional_field(
    line: Option<&str>,
    prefix: &str,
    max_encoded_bytes: usize,
) -> Result<String, CanvasOAuthError> {
    let value = line
        .and_then(|line| line.strip_prefix(prefix))
        .ok_or_else(|| CanvasOAuthError::new(CanvasOAuthFailure::MalformedRecord))?;
    if value.len() > max_encoded_bytes {
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
    client_id: String,
    redirect_uri: String,
    access_token: Option<CanvasAccessToken>,
    refresh_token: Option<CanvasAccessToken>,
    expires_at: Option<u64>,
    display_name: String,
    needs_reconnect: bool,
}

impl CanvasOAuthRecord {
    pub(crate) fn new(
        origin: CanvasOrigin,
        client_id: String,
        redirect_uri: String,
        access_token: CanvasAccessToken,
        refresh_token: CanvasAccessToken,
        expires_at: u64,
        display_name: String,
    ) -> Self {
        Self {
            origin,
            client_id,
            redirect_uri,
            access_token: Some(access_token),
            refresh_token: Some(refresh_token),
            expires_at: Some(expires_at),
            display_name,
            needs_reconnect: false,
        }
    }

    pub(crate) fn reconnect_required(
        origin: CanvasOrigin,
        client_id: String,
        redirect_uri: String,
        display_name: String,
    ) -> Self {
        Self {
            origin,
            client_id,
            redirect_uri,
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
            "{OAUTH_RECORD_PREFIX}\nauth_mode=oauth\norigin_hex={}\nclient_id_hex={}\nredirect_uri_hex={}\naccess_token_hex={}\nrefresh_token_hex={}\nexpires_at={}\ndisplay_name_hex={}\nneeds_reconnect={}",
            encode_hex(self.origin.as_str().as_bytes()),
            encode_hex(self.client_id.as_bytes()),
            encode_hex(self.redirect_uri.as_bytes()),
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
        let origin_hex = required_field(lines.next(), "origin_hex=", 1024)?;
        let client_id_hex = required_field(lines.next(), "client_id_hex=", MAX_CLIENT_ID_BYTES * 2)?;
        let redirect_hex =
            required_field(lines.next(), "redirect_uri_hex=", MAX_REDIRECT_URI_BYTES * 2)?;
        let access_hex = optional_field(lines.next(), "access_token_hex=", MAX_TOKEN_BYTES * 2)?;
        let refresh_hex = optional_field(lines.next(), "refresh_token_hex=", MAX_TOKEN_BYTES * 2)?;
        let expires_raw = optional_field(lines.next(), "expires_at=", 32)?;
        let display_hex =
            optional_field(lines.next(), "display_name_hex=", MAX_DISPLAY_NAME_BYTES * 2)?;
        let reconnect_raw = required_field(lines.next(), "needs_reconnect=", 2)?;
        if lines.next().is_some() {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::MalformedRecord,
            ));
        }
        let origin = decode_hex_to_string(&origin_hex, 512)
            .and_then(|value| CanvasOrigin::parse(&value))
            .map_err(map_connection_error)?;
        let client_id = decode_hex_to_string(&client_id_hex, MAX_CLIENT_ID_BYTES)
            .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::MalformedRecord))?;
        let redirect_uri = decode_hex_to_string(&redirect_hex, MAX_REDIRECT_URI_BYTES)
            .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::MalformedRecord))?;
        let access = optional_token(&access_hex)?;
        let refresh = optional_token(&refresh_hex)?;
        let expires_at = if expires_raw.is_empty() {
            None
        } else {
            Some(
                expires_raw
                    .parse::<u64>()
                    .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::MalformedRecord))?,
            )
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
        if needs_reconnect {
            if access.is_some() || refresh.is_some() || expires_at.is_some() {
                return Err(CanvasOAuthError::new(
                    CanvasOAuthFailure::MalformedRecord,
                ));
            }
        } else if access.is_none() || expires_at.is_none() {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::MalformedRecord,
            ));
        }
        Ok(Self {
            origin,
            client_id,
            redirect_uri,
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

    pub(crate) fn client_id(&self) -> &str {
        &self.client_id
    }

    pub(crate) fn redirect_uri(&self) -> &str {
        &self.redirect_uri
    }

    pub(crate) fn display_name(&self) -> &str {
        &self.display_name
    }

    pub(crate) fn access_token(&self) -> Option<&CanvasAccessToken> {
        self.access_token.as_ref()
    }

    pub(crate) fn refresh_token(&self) -> Option<&CanvasAccessToken> {
        self.refresh_token.as_ref()
    }

    pub(crate) fn is_renewable(&self) -> bool {
        !self.needs_reconnect
            && self.access_token.is_some()
            && self.refresh_token.is_some()
            && self.expires_at.is_some()
    }

    pub(crate) fn is_expired(&self, now: u64) -> bool {
        match self.expires_at {
            Some(expires_at) => now + REFRESH_MARGIN_SECONDS >= expires_at,
            None => true,
        }
    }
}

impl fmt::Debug for CanvasOAuthRecord {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CanvasOAuthRecord")
            .field("origin", &self.origin)
            .field("client_id", &self.client_id)
            .field("redirect_uri", &self.redirect_uri)
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
    let value = decode_hex_to_string(encoded, MAX_TOKEN_BYTES)
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

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct CanvasDisconnectResult {
    pub(crate) local_disconnected: bool,
    pub(crate) remote_revoked: bool,
    pub(crate) remote_revoke_attempted: bool,
    pub(crate) remote_revoke_failed: bool,
}

#[derive(Clone)]
struct PendingOAuthState {
    state: String,
    created_at: u64,
}

pub(crate) struct CanvasCredentialManager<S: CredentialStore> {
    profile: RuntimeProfile,
    store: S,
    http: Arc<dyn CanvasOAuthHttp + Send + Sync>,
    now: NowFn,
    refresh_lock: Mutex<()>,
    pending_oauth: Mutex<Option<PendingOAuthState>>,
}

impl CanvasCredentialManager<CredentialStoreBackend> {
    pub(crate) fn for_profile(profile: RuntimeProfile) -> Self {
        Self::new(
            profile,
            crate::credential_store::credential_store_for_profile(profile),
            Arc::new(UreqCanvasOAuthHttp),
            Arc::new(system_now_seconds),
        )
    }
}

impl<S: CredentialStore> CanvasCredentialManager<S> {
    pub(crate) fn new(
        profile: RuntimeProfile,
        store: S,
        http: Arc<dyn CanvasOAuthHttp + Send + Sync>,
        now: NowFn,
    ) -> Self {
        Self {
            profile,
            store,
            http,
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
        if let Some(origin) = self.active_connecting_origin() {
            return CanvasStatus {
                state: CanvasConnectionState::Connecting,
                auth_mode: None,
                origin: Some(origin),
                display_name: None,
            };
        }

        match self.load_record(CredentialSlot::CanvasOAuth) {
            Ok(record) => {
                if record.needs_reconnect {
                    return CanvasStatus {
                        state: CanvasConnectionState::ReconnectRequired,
                        auth_mode: Some("oauth"),
                        origin: Some(record.origin.as_str().to_string()),
                        display_name: nonempty(record.display_name()),
                    };
                }
                if record.is_renewable() {
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
            Err(error) if error.failure() == CanvasOAuthFailure::Missing => {}
            Err(_) => {
                return CanvasStatus {
                    state: CanvasConnectionState::Error,
                    auth_mode: None,
                    origin: None,
                    display_name: None,
                };
            }
        }

        if self.profile == RuntimeProfile::Development {
            if let Ok(secret) = self
                .store
                .get_for_trusted_native_use(CredentialSlot::CanvasDefault)
            {
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

    fn active_connecting_origin(&self) -> Option<String> {
        let now = (self.now)();
        let guard = self.pending_oauth.lock().expect("oauth lock");
        let pending = guard.as_ref()?;
        if now >= pending.created_at.saturating_add(OAUTH_STATE_TTL_SECONDS) {
            return None;
        }
        self.load_client_config()
            .ok()
            .map(|config| config.origin().as_str().to_string())
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
            needs_code_input: true,
        })
    }

    pub(crate) fn cancel_oauth(&self) {
        *self.pending_oauth.lock().expect("oauth lock") = None;
    }

    pub(crate) fn complete_oauth(&self, response_url: &str) -> Result<CanvasStatus, CanvasOAuthError> {
        let config = self.load_client_config()?;
        let callback = parse_oauth_response(response_url, config.origin())?;
        let pending = self.peek_pending_state()?;
        if pending != callback.state {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::StateMismatch,
            ));
        }
        let _consumed = self.take_pending_state()?;
        match callback.outcome {
            OAuthOutcome::Denied => Err(CanvasOAuthError::new(
                CanvasOAuthFailure::AccessDenied,
            )),
            OAuthOutcome::Code(code) => self.finish_oauth(&config, &code),
        }
    }

    fn peek_pending_state(&self) -> Result<String, CanvasOAuthError> {
        let now = (self.now)();
        let guard = self.pending_oauth.lock().expect("oauth lock");
        let pending = guard
            .as_ref()
            .ok_or_else(|| CanvasOAuthError::new(CanvasOAuthFailure::StateMissing))?;
        if now >= pending.created_at.saturating_add(OAUTH_STATE_TTL_SECONDS) {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::StateExpired,
            ));
        }
        Ok(pending.state.clone())
    }

    fn take_pending_state(&self) -> Result<String, CanvasOAuthError> {
        let now = (self.now)();
        let mut guard = self.pending_oauth.lock().expect("oauth lock");
        let pending = guard
            .take()
            .ok_or_else(|| CanvasOAuthError::new(CanvasOAuthFailure::StateMissing))?;
        if now >= pending.created_at.saturating_add(OAUTH_STATE_TTL_SECONDS) {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::StateExpired,
            ));
        }
        Ok(pending.state)
    }

    fn finish_oauth(
        &self,
        config: &CanvasOAuthClientConfig,
        code: &str,
    ) -> Result<CanvasStatus, CanvasOAuthError> {
        let code = code.trim();
        if code.is_empty() || code.len() > MAX_CODE_BYTES {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::ExchangeFailed,
            ));
        }
        let token_payload = self.exchange_code(config, code)?;
        let access_token = CanvasAccessToken::new(token_payload.access_token)
            .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::ExchangeFailed))?;
        let refresh_token = CanvasAccessToken::new(
            token_payload
                .refresh_token
                .ok_or_else(|| CanvasOAuthError::new(CanvasOAuthFailure::ExchangeFailed))?,
        )
            .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::ExchangeFailed))?;
        let display_name = self.validate_identity(config.origin(), &access_token)?;
        let expires_at = (self.now)().saturating_add(token_payload.expires_in);
        let record = CanvasOAuthRecord::new(
            config.origin().clone(),
            config.client_id.clone(),
            config.redirect.as_str().to_string(),
            access_token,
            refresh_token,
            expires_at,
            display_name.clone(),
        );
        self.store
            .store_replace(
                CredentialSlot::CanvasOAuth,
                &SecretValue::new(record.serialize()),
            )
            .map_err(map_store_error)?;
        Ok(CanvasStatus {
            state: CanvasConnectionState::Connected,
            auth_mode: Some("oauth"),
            origin: Some(config.origin().as_str().to_string()),
            display_name: nonempty(&display_name),
        })
    }

    fn exchange_code(
        &self,
        config: &CanvasOAuthClientConfig,
        code: &str,
    ) -> Result<TokenPayload, CanvasOAuthError> {
        let url = format!("{}{}", config.origin().as_str(), OAUTH_TOKEN_PATH);
        let client_secret = config
            .client_secret
            .expose_for_trusted_native_use()
            .to_string();
        let request = CanvasOAuthRequest {
            method: "POST",
            url,
            headers: Vec::new(),
            form: vec![
                ("grant_type".to_string(), "authorization_code".to_string()),
                ("client_id".to_string(), config.client_id.clone()),
                ("client_secret".to_string(), client_secret),
                ("redirect_uri".to_string(), config.redirect.as_str().to_string()),
                ("code".to_string(), code.to_string()),
            ],
        };
        let response = self.http.send(request)?;
        let status = classify_status(response.status);
        if let Some(failure) = status {
            return Err(CanvasOAuthError::new(failure));
        }
        parse_token_payload(&response.body, true)
    }

    fn validate_identity(
        &self,
        origin: &CanvasOrigin,
        access_token: &CanvasAccessToken,
    ) -> Result<String, CanvasOAuthError> {
        let url = format!("{}{}", origin.as_str(), OAUTH_IDENTITY_PATH);
        let request = CanvasOAuthRequest {
            method: "GET",
            url,
            headers: vec![(
                "Authorization".to_string(),
                format!("Bearer {}", access_token.expose_for_authorized_canvas_request()),
            )],
            form: Vec::new(),
        };
        let response = self.http.send(request)?;
        let status = classify_status(response.status);
        if let Some(failure) = status {
            return Err(CanvasOAuthError::new(
                failure_for_identity(failure),
            ));
        }
        let payload: serde_json::Value = serde_json::from_str(&response.body)
            .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::IdentityFailed))?;
        if payload.get("id").is_none() {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::IdentityFailed,
            ));
        }
        let display_name = payload
            .get("name")
            .or_else(|| payload.get("short_name"))
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
            .to_string();
        Ok(display_name)
    }

    pub(crate) fn resolve_canvas_default(&self) -> Result<SecretValue, CanvasOAuthError> {
        match self.load_record(CredentialSlot::CanvasOAuth) {
            Ok(record) => {
                if record.needs_reconnect {
                    return Err(CanvasOAuthError::new(
                        CanvasOAuthFailure::ReconnectRequired,
                    ));
                }
                if let Some(access_token) = record.access_token() {
                    if !record.is_expired((self.now)()) {
                        return Ok(SecretValue::new(project_record(&record, access_token)));
                    }
                }
                let _guard = self.refresh_lock.lock().expect("refresh lock");
                let reloaded = self.load_record(CredentialSlot::CanvasOAuth)?;
                if reloaded.needs_reconnect {
                    return Err(CanvasOAuthError::new(
                        CanvasOAuthFailure::ReconnectRequired,
                    ));
                }
                if let Some(access_token) = reloaded.access_token() {
                    if !reloaded.is_expired((self.now)()) {
                        return Ok(SecretValue::new(project_record(&reloaded, access_token)));
                    }
                }
                self.refresh_and_project_locked(&reloaded)
            }
            Err(error) if error.failure() == CanvasOAuthFailure::Missing => {
                self.resolve_development_pat_or_missing()
            }
            Err(error) => Err(error),
        }
    }

    fn resolve_development_pat_or_missing(&self) -> Result<SecretValue, CanvasOAuthError> {
        if self.profile == RuntimeProfile::Development {
            match self
                .store
                .get_for_trusted_native_use(CredentialSlot::CanvasDefault)
            {
                Ok(secret) => return Ok(secret),
                Err(error) if error.failure() == CredentialStoreFailure::Missing => {}
                Err(error) => return Err(map_store_error(error)),
            }
        }
        Err(CanvasOAuthError::new(CanvasOAuthFailure::Missing))
    }

    fn refresh_and_project_locked(
        &self,
        record: &CanvasOAuthRecord,
    ) -> Result<SecretValue, CanvasOAuthError> {
        let config = self.load_client_config()?;
        if record.origin() != config.origin()
            || record.client_id() != config.client_id
            || record.redirect_uri() != config.redirect.as_str()
        {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::ClientBindingMismatch,
            ));
        }
        let refresh_token = record
            .refresh_token
            .as_ref()
            .ok_or_else(|| CanvasOAuthError::new(CanvasOAuthFailure::ReconnectRequired))?;
        let client_secret = config
            .client_secret
            .expose_for_trusted_native_use()
            .to_string();
        let request = CanvasOAuthRequest {
            method: "POST",
            url: format!("{}{}", config.origin().as_str(), OAUTH_TOKEN_PATH),
            headers: Vec::new(),
            form: vec![
                ("grant_type".to_string(), "refresh_token".to_string()),
                ("client_id".to_string(), config.client_id.clone()),
                ("client_secret".to_string(), client_secret),
                (
                    "redirect_uri".to_string(),
                    config.redirect.as_str().to_string(),
                ),
                (
                    "refresh_token".to_string(),
                    refresh_token.expose_for_authorized_canvas_request().to_string(),
                ),
            ],
        };
        match self.http.send(request) {
            Ok(response) => {
                if !(200..300).contains(&response.status) {
                    let token_error = parse_oauth_token_error(&response.body);
                    if response.status == 400
                        && token_error.as_deref() == Some("invalid_grant")
                    {
                        return self.tombstone_refresh(record);
                    }
                    if token_error.as_deref() == Some("invalid_client") {
                        return Err(CanvasOAuthError::new(
                            CanvasOAuthFailure::InvalidClientConfig,
                        ));
                    }
                    if response.status == 401 || response.status == 403 {
                        return self.tombstone_refresh(record);
                    }
                    return Err(CanvasOAuthError::new(
                        classify_status(response.status)
                            .unwrap_or(CanvasOAuthFailure::ExchangeFailed),
                    ));
                }
                let payload = parse_token_payload(&response.body, false)?;
                let access_token = CanvasAccessToken::new(payload.access_token)
                    .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::ExchangeFailed))?;
                let refresh_token = if let Some(value) = payload.refresh_token {
                    Some(
                        CanvasAccessToken::new(value)
                            .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::ExchangeFailed))?,
                    )
                } else {
                    record.refresh_token.clone()
                };
                let expires_at = (self.now)().saturating_add(payload.expires_in);
                let updated = CanvasOAuthRecord {
                    origin: record.origin().clone(),
                    client_id: record.client_id().to_string(),
                    redirect_uri: record.redirect_uri().to_string(),
                    access_token: Some(access_token.clone()),
                    refresh_token,
                    expires_at: Some(expires_at),
                    display_name: record.display_name().to_string(),
                    needs_reconnect: false,
                };
                self.store
                    .store_replace(
                        CredentialSlot::CanvasOAuth,
                        &SecretValue::new(updated.serialize()),
                    )
                    .map_err(map_store_error)?;
                Ok(SecretValue::new(project_record(&updated, &access_token)))
            }
            Err(error) => Err(error),
        }
    }

    fn tombstone_refresh(&self, record: &CanvasOAuthRecord) -> Result<SecretValue, CanvasOAuthError> {
        let reconnect = CanvasOAuthRecord::reconnect_required(
            record.origin().clone(),
            record.client_id().to_string(),
            record.redirect_uri().to_string(),
            record.display_name().to_string(),
        );
        self.store
            .store_replace(
                CredentialSlot::CanvasOAuth,
                &SecretValue::new(reconnect.serialize()),
            )
            .map_err(map_store_error)?;
        Err(CanvasOAuthError::new(
            CanvasOAuthFailure::ReconnectRequired,
        ))
    }

    pub(crate) fn disconnect(
        &self,
        revoke: bool,
    ) -> Result<CanvasDisconnectResult, CanvasOAuthError> {
        let mut result = CanvasDisconnectResult {
            local_disconnected: false,
            remote_revoked: false,
            remote_revoke_attempted: revoke,
            remote_revoke_failed: false,
        };
        if revoke {
            match self.revoke_remote() {
                Ok(revoked) => result.remote_revoked = revoked,
                Err(_) => result.remote_revoke_failed = true,
            }
        }
        for slot in [CredentialSlot::CanvasDefault, CredentialSlot::CanvasOAuth] {
            match self.store.delete(slot) {
                Ok(()) => {}
                Err(error) if error.failure() == CredentialStoreFailure::Missing => {}
                Err(error) => return Err(map_store_error(error)),
            }
        }
        self.cancel_oauth();
        result.local_disconnected = true;
        Ok(result)
    }

    fn revoke_remote(&self) -> Result<bool, CanvasOAuthError> {
        let record = match self.load_record(CredentialSlot::CanvasOAuth) {
            Ok(record) => record,
            Err(_) => return Ok(false),
        };
        let Some(access_token) = record.access_token() else {
            return Ok(false);
        };
        let request = CanvasOAuthRequest {
            method: "DELETE",
            url: format!("{}{}", record.origin().as_str(), OAUTH_TOKEN_PATH),
            headers: vec![(
                "Authorization".to_string(),
                format!("Bearer {}", access_token.expose_for_authorized_canvas_request()),
            )],
            form: Vec::new(),
        };
        let response = self.http.send(request)?;
        if (200..300).contains(&response.status) {
            Ok(true)
        } else if let Some(failure) = classify_status(response.status) {
            Err(CanvasOAuthError::new(failure))
        } else {
            Err(CanvasOAuthError::new(
                CanvasOAuthFailure::ServerFailure,
            ))
        }
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

fn project_record(record: &CanvasOAuthRecord, access_token: &CanvasAccessToken) -> String {
    crate::canvas_connection::CanvasConnectionRecord::new(
        record.origin().clone(),
        access_token.clone(),
    )
    .serialize()
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
    url.push_str("&scope=");
    url.push_str(&urlencode(&CANVAS_READ_SCOPES.join(" ")));
    if url.len() > MAX_RESPONSE_URL_BYTES {
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

#[derive(Debug, Eq, PartialEq)]
enum OAuthOutcome {
    Code(String),
    Denied,
}

struct OAuthCallback {
    state: String,
    outcome: OAuthOutcome,
}

fn parse_oauth_response(
    raw: &str,
    origin: &CanvasOrigin,
) -> Result<OAuthCallback, CanvasOAuthError> {
    let value = raw.trim();
    if value.is_empty() || value.len() > MAX_RESPONSE_URL_BYTES {
        return Err(CanvasOAuthError::new(
            CanvasOAuthFailure::MalformedResponse,
        ));
    }
    let parsed = tauri::Url::parse(value)
        .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::MalformedResponse))?;
    if parsed.scheme() != "https"
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.fragment().is_some()
    {
        return Err(CanvasOAuthError::new(
            CanvasOAuthFailure::MalformedResponse,
        ));
    }
    if parsed.path() != OAUTH_RESPONSE_PATH || !url_matches_origin(&parsed, origin) {
        return Err(CanvasOAuthError::new(
            CanvasOAuthFailure::MalformedResponse,
        ));
    }

    let mut state: Option<String> = None;
    let mut code: Option<String> = None;
    let mut error: Option<String> = None;
    for (key, value) in parsed.query_pairs() {
        match key.as_ref() {
            "state" => {
                if state.is_some() || value.is_empty() || value.len() > MAX_STATE_BYTES {
                    return Err(CanvasOAuthError::new(
                        CanvasOAuthFailure::MalformedResponse,
                    ));
                }
                state = Some(value.into_owned());
            }
            "code" => {
                if code.is_some() || value.is_empty() || value.len() > MAX_CODE_BYTES {
                    return Err(CanvasOAuthError::new(
                        CanvasOAuthFailure::MalformedResponse,
                    ));
                }
                code = Some(value.into_owned());
            }
            "error" => {
                if error.is_some() || value.is_empty() || value.len() > 256 {
                    return Err(CanvasOAuthError::new(
                        CanvasOAuthFailure::MalformedResponse,
                    ));
                }
                error = Some(value.into_owned());
            }
            "access_token" | "client_secret" | "refresh_token" | "token" => {
                return Err(CanvasOAuthError::new(
                    CanvasOAuthFailure::MalformedResponse,
                ));
            }
            _ => {}
        }
    }
    let state = state.ok_or_else(|| {
        CanvasOAuthError::new(CanvasOAuthFailure::StateMissing)
    })?;
    match (code, error) {
        (Some(code), None) => Ok(OAuthCallback {
            state,
            outcome: OAuthOutcome::Code(code),
        }),
        (None, Some(error)) if error == "access_denied" => Ok(OAuthCallback {
            state,
            outcome: OAuthOutcome::Denied,
        }),
        _ => Err(CanvasOAuthError::new(
            CanvasOAuthFailure::MalformedResponse,
        )),
    }
}

fn url_matches_origin(parsed: &tauri::Url, origin: &CanvasOrigin) -> bool {
    let Ok(origin_url) = tauri::Url::parse(origin.as_str()) else {
        return false;
    };
    let host_matches = parsed
        .host_str()
        .map(|host| host.to_ascii_lowercase())
        == origin_url
            .host_str()
            .map(|host| host.to_ascii_lowercase());
    let port_matches = parsed.port_or_known_default() == origin_url.port_or_known_default();
    host_matches && port_matches
}

struct TokenPayload {
    access_token: String,
    refresh_token: Option<String>,
    expires_in: u64,
}

fn parse_token_payload(body: &str, require_refresh: bool) -> Result<TokenPayload, CanvasOAuthError> {
    let payload: serde_json::Value = serde_json::from_str(body)
        .map_err(|_| CanvasOAuthError::new(CanvasOAuthFailure::ExchangeFailed))?;
    if let Some(token_type) = payload.get("token_type").and_then(serde_json::Value::as_str) {
        if !token_type.eq_ignore_ascii_case("Bearer") {
            return Err(CanvasOAuthError::new(
                CanvasOAuthFailure::ExchangeFailed,
            ));
        }
    }
    let access_token = payload
        .get("access_token")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| CanvasOAuthError::new(CanvasOAuthFailure::ExchangeFailed))?
        .to_string();
    if access_token.trim().is_empty() || access_token.len() > MAX_TOKEN_BYTES {
        return Err(CanvasOAuthError::new(
            CanvasOAuthFailure::ExchangeFailed,
        ));
    }
    let expires_in = payload
        .get("expires_in")
        .and_then(serde_json::Value::as_u64)
        .filter(|value| *value > 0)
        .ok_or_else(|| CanvasOAuthError::new(CanvasOAuthFailure::ExchangeFailed))?;
    let refresh_token = payload
        .get("refresh_token")
        .and_then(serde_json::Value::as_str)
        .map(|value| value.to_string())
        .filter(|value| !value.is_empty() && value.len() <= MAX_TOKEN_BYTES);
    if require_refresh && refresh_token.is_none() {
        return Err(CanvasOAuthError::new(
            CanvasOAuthFailure::ExchangeFailed,
        ));
    }
    Ok(TokenPayload {
        access_token,
        refresh_token,
        expires_in,
    })
}

fn classify_status(status: u16) -> Option<CanvasOAuthFailure> {
    if (200..300).contains(&status) {
        return None;
    }
    match status {
        401 => Some(CanvasOAuthFailure::Unauthorized),
        403 => Some(CanvasOAuthFailure::Forbidden),
        429 => Some(CanvasOAuthFailure::RateLimited),
        value if value >= 500 => Some(CanvasOAuthFailure::ServerFailure),
        _ => Some(CanvasOAuthFailure::ExchangeFailed),
    }
}

fn failure_for_identity(failure: CanvasOAuthFailure) -> CanvasOAuthFailure {
    match failure {
        CanvasOAuthFailure::Unauthorized
        | CanvasOAuthFailure::Forbidden
        | CanvasOAuthFailure::RateLimited
        | CanvasOAuthFailure::ServerFailure
        | CanvasOAuthFailure::TransportFailure => failure,
        _ => CanvasOAuthFailure::IdentityFailed,
    }
}

fn parse_oauth_token_error(body: &str) -> Option<String> {
    const RECOGNIZED: &[&str] = &[
        "invalid_grant",
        "invalid_client",
        "invalid_request",
        "unsupported_grant_type",
    ];
    let payload: serde_json::Value = serde_json::from_str(body).ok()?;
    let code = payload.get("error").and_then(serde_json::Value::as_str)?;
    if code.is_empty() || code.len() > 256 || !RECOGNIZED.contains(&code) {
        return None;
    }
    Some(code.to_string())
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
    use crate::credential_store::{namespace_for_profile, MemoryCredentialStore};
    use std::sync::atomic::{AtomicU64, Ordering};

    const CLIENT_ID: &str = "synthetic-client-id";
    const CLIENT_SECRET: &str = "synthetic-client-secret";
    const ACCESS: &str = "synthetic-access-token";
    const REFRESH: &str = "synthetic-refresh-token";
    const ORIGIN: &str = "https://canvas.example.edu";

    #[derive(Default)]
    struct FakeHttp {
        requests: Mutex<Vec<CanvasOAuthRequest>>,
        responses: Mutex<Vec<CanvasOAuthHttpResponse>>,
    }

    impl FakeHttp {
        fn queue(&self, status: u16, body: &str) {
            self.responses.lock().expect("test lock").push(
                CanvasOAuthHttpResponse {
                    status,
                    body: body.to_string(),
                },
            );
        }

        fn requests(&self) -> Vec<CanvasOAuthRequest> {
            self.requests.lock().expect("test lock").clone()
        }

        fn token_body(access: &str, refresh: Option<&str>, expires_in: u64) -> String {
            let refresh_json = refresh
                .map(|value| format!(",\"refresh_token\":\"{value}\""))
                .unwrap_or_default();
            format!(
                "{{\"access_token\":\"{access}\",\"token_type\":\"Bearer\",\"expires_in\":{expires_in}{refresh_json}}}"
            )
        }
    }

    impl CanvasOAuthHttp for FakeHttp {
        fn send(
            &self,
            request: CanvasOAuthRequest,
        ) -> Result<CanvasOAuthHttpResponse, CanvasOAuthError> {
            self.requests
                .lock()
                .expect("test lock")
                .push(request);
            let mut responses = self.responses.lock().expect("test lock");
            if responses.is_empty() {
                return Err(CanvasOAuthError::new(CanvasOAuthFailure::TransportFailure));
            }
            Ok(responses.remove(0))
        }
    }

    fn manager(
        profile: RuntimeProfile,
        http: Arc<dyn CanvasOAuthHttp + Send + Sync>,
    ) -> (
        Arc<CanvasCredentialManager<MemoryCredentialStore>>,
        Arc<AtomicU64>,
    ) {
        let clock = Arc::new(AtomicU64::new(1_700_000_000));
        let now = {
            let clock = clock.clone();
            Arc::new(move || clock.load(Ordering::SeqCst)) as NowFn
        };
        let store = MemoryCredentialStore::new(namespace_for_profile(profile));
        (
            Arc::new(CanvasCredentialManager::new(profile, store, http, now)),
            clock,
        )
    }

    fn provisioned(manager: &CanvasCredentialManager<MemoryCredentialStore>) {
        manager
            .provision_client_config(ORIGIN, CLIENT_ID, CLIENT_SECRET, OAUTH_OOB_REDIRECT)
            .expect("provision synthetic client");
    }

    fn extract_state(auth_url: &str) -> String {
        let parsed = tauri::Url::parse(auth_url).expect("auth url");
        parsed
            .query_pairs()
            .find(|(key, _)| key == "state")
            .expect("state")
            .1
            .into_owned()
    }

    fn response_url(state: &str, code: &str) -> String {
        format!(
            "{}{}?state={}&code={}",
            ORIGIN,
            OAUTH_RESPONSE_PATH,
            urlencode(state),
            urlencode(code)
        )
    }

    #[test]
    fn auth_url_contains_exact_read_scopes() {
        let http = Arc::new(FakeHttp::default());
        let (manager, _clock) = manager(RuntimeProfile::Development, http);
        provisioned(&manager);
        let start = manager.begin_oauth().expect("begin");
        let parsed = tauri::Url::parse(&start.auth_url).expect("auth url");
        assert_eq!(parsed.scheme(), "https");
        assert_eq!(parsed.host_str(), Some("canvas.example.edu"));
        assert_eq!(parsed.path(), OAUTH_AUTH_PATH);
        let scopes: Vec<String> = parsed
            .query_pairs()
            .filter(|(key, _)| key == "scope")
            .map(|(_, value)| value.into_owned())
            .collect();
        assert_eq!(scopes.len(), 1);
        let mut scope_items: Vec<&str> = scopes[0].split(' ').collect();
        scope_items.sort_unstable();
        let mut expected: Vec<&str> = CANVAS_READ_SCOPES.to_vec();
        expected.sort_unstable();
        assert_eq!(scope_items, expected);
        assert!(start.auth_url.contains("redirect_uri=urn%3Aietf%3Awg%3Aoauth%3A2.0%3Aoob"));
        assert!(start.auth_url.contains("response_type=code"));
    }

    #[test]
    fn state_mismatch_and_missing_do_not_exchange() {
        let http = Arc::new(FakeHttp::default());
        let (manager, _clock) = manager(RuntimeProfile::Development, http.clone());
        provisioned(&manager);
        let start = manager.begin_oauth().expect("begin");
        let state = extract_state(&start.auth_url);
        let wrong = response_url("wrong-state", "synthetic-code");
        assert_eq!(
            manager.complete_oauth(&wrong).unwrap_err().failure(),
            CanvasOAuthFailure::StateMismatch
        );
        assert!(http.requests().is_empty());
        http.queue(200, &FakeHttp::token_body(ACCESS, Some(REFRESH), 3600));
        http.queue(200, "{\"id\":42,\"name\":\"Synthetic Student\"}");
        let correct = response_url(&state, "synthetic-code");
        manager.complete_oauth(&correct).expect("complete");
        assert_eq!(http.requests().len(), 2); // token exchange + identity
        assert_eq!(
            manager
                .complete_oauth(&correct)
                .unwrap_err()
                .failure(),
            CanvasOAuthFailure::StateMissing
        );
    }

    #[test]
    fn access_denied_has_no_exchange_or_write() {
        let http = Arc::new(FakeHttp::default());
        let (manager, _clock) = manager(RuntimeProfile::Development, http.clone());
        provisioned(&manager);
        let start = manager.begin_oauth().expect("begin");
        let state = extract_state(&start.auth_url);
        let denied = format!(
            "{}{}?state={}&error=access_denied",
            ORIGIN,
            OAUTH_RESPONSE_PATH,
            urlencode(&state)
        );
        assert_eq!(
            manager.complete_oauth(&denied).unwrap_err().failure(),
            CanvasOAuthFailure::AccessDenied
        );
        assert!(http.requests().is_empty());
        assert_eq!(manager.status().state, CanvasConnectionState::NotConnected);
    }

    #[test]
    fn exchange_and_identity_requests_are_exact() {
        let http = Arc::new(FakeHttp::default());
        http.queue(200, &FakeHttp::token_body(ACCESS, Some(REFRESH), 3600));
        http.queue(200, "{\"id\":42,\"name\":\"Synthetic Student\"}");
        let (manager, _clock) = manager(RuntimeProfile::Development, http.clone());
        provisioned(&manager);
        let start = manager.begin_oauth().expect("begin");
        let state = extract_state(&start.auth_url);
        let status = manager
            .complete_oauth(&response_url(&state, "synthetic-code"))
            .expect("complete");
        assert_eq!(status.state, CanvasConnectionState::Connected);
        assert_eq!(status.display_name.as_deref(), Some("Synthetic Student"));
        let requests = http.requests();
        assert_eq!(requests.len(), 2);
        assert_eq!(requests[0].method, "POST");
        assert_eq!(requests[0].url, format!("{ORIGIN}{OAUTH_TOKEN_PATH}"));
        let fields: Vec<(&str, &str)> = requests[0]
            .form
            .iter()
            .map(|(a, b)| (a.as_str(), b.as_str()))
            .collect();
        assert!(fields.contains(&("grant_type", "authorization_code")));
        assert!(fields.contains(&("client_id", CLIENT_ID)));
        assert!(fields.contains(&("client_secret", CLIENT_SECRET)));
        assert!(fields.contains(&("redirect_uri", OAUTH_OOB_REDIRECT)));
        assert!(fields.contains(&("code", "synthetic-code")));
        assert_eq!(requests[1].method, "GET");
        assert_eq!(requests[1].url, format!("{ORIGIN}{OAUTH_IDENTITY_PATH}"));
        assert_eq!(
            requests[1].headers,
            vec![("Authorization".to_string(), format!("Bearer {ACCESS}"))]
        );
    }

    #[test]
    fn identity_failure_persists_nothing() {
        let http = Arc::new(FakeHttp::default());
        http.queue(200, &FakeHttp::token_body(ACCESS, Some(REFRESH), 3600));
        http.queue(401, "{}");
        let (manager, _clock) = manager(RuntimeProfile::Development, http.clone());
        provisioned(&manager);
        let start = manager.begin_oauth().expect("begin");
        let state = extract_state(&start.auth_url);
        assert_eq!(
            manager
                .complete_oauth(&response_url(&state, "synthetic-code"))
                .unwrap_err()
                .failure(),
            CanvasOAuthFailure::Unauthorized
        );
        assert!(manager.load_record(CredentialSlot::CanvasOAuth).is_err());
    }

    #[test]
    fn refresh_request_includes_redirect_uri() {
        let http = Arc::new(FakeHttp::default());
        http.queue(
            200,
            &FakeHttp::token_body("synthetic-new-access", None, 3600),
        );
        let (manager, clock) = manager(RuntimeProfile::Development, http.clone());
        provisioned(&manager);
        let record = CanvasOAuthRecord::new(
            CanvasOrigin::parse(ORIGIN).expect("origin"),
            CLIENT_ID.to_string(),
            OAUTH_OOB_REDIRECT.to_string(),
            CanvasAccessToken::new(ACCESS).expect("access"),
            CanvasAccessToken::new(REFRESH).expect("refresh"),
            1_700_000_000,
            "Synthetic Student".to_string(),
        );
        manager
            .store
            .store_replace(
                CredentialSlot::CanvasOAuth,
                &SecretValue::new(record.serialize()),
            )
            .expect("store");
        clock.store(1_700_000_100, Ordering::SeqCst);
        let projected = manager.resolve_canvas_default().expect("resolve");
        let parsed = crate::canvas_connection::CanvasConnectionRecord::parse(
            projected.expose_for_trusted_native_use(),
        )
        .expect("projection");
        assert_eq!(parsed.origin().as_str(), ORIGIN);
        let requests = http.requests();
        assert_eq!(requests.len(), 1);
        assert_eq!(requests[0].method, "POST");
        let fields: Vec<(&str, &str)> = requests[0]
            .form
            .iter()
            .map(|(a, b)| (a.as_str(), b.as_str()))
            .collect();
        assert!(fields.contains(&("grant_type", "refresh_token")));
        assert!(fields.contains(&("redirect_uri", OAUTH_OOB_REDIRECT)));
        assert!(fields.contains(&("refresh_token", REFRESH)));
    }

    #[test]
    fn reconnect_record_round_trips_and_survives_restart() {
        let http = Arc::new(FakeHttp::default());
        let (manager, _clock) = manager(RuntimeProfile::Development, http.clone());
        provisioned(&manager);
        let reconnect = CanvasOAuthRecord::reconnect_required(
            CanvasOrigin::parse(ORIGIN).expect("origin"),
            CLIENT_ID.to_string(),
            OAUTH_OOB_REDIRECT.to_string(),
            "Synthetic Student".to_string(),
        );
        let serialized = reconnect.serialize();
        let parsed = CanvasOAuthRecord::parse(&serialized).expect("parse");
        assert!(parsed.needs_reconnect);
        assert!(parsed.access_token().is_none());
        assert!(parsed.refresh_token.is_none());
        assert!(parsed.expires_at.is_none());

        manager
            .store
            .store_replace(
                CredentialSlot::CanvasOAuth,
                &SecretValue::new(serialized),
            )
            .expect("store reconnect");
        let second = CanvasCredentialManager::new(
            RuntimeProfile::Development,
            manager.store.clone(),
            http,
            manager.now.clone(),
        );
        assert_eq!(
            second.status().state,
            CanvasConnectionState::ReconnectRequired
        );
        assert_eq!(
            second
                .resolve_canvas_default()
                .unwrap_err()
                .failure(),
            CanvasOAuthFailure::ReconnectRequired
        );
    }

    #[test]
    fn production_never_falls_back_to_legacy_canvas_default() {
        let http = Arc::new(FakeHttp::default());
        let (manager, _clock) = manager(RuntimeProfile::Production, http);
        manager
            .store
            .store_replace(
                CredentialSlot::CanvasDefault,
                &SecretValue::new(
                    crate::canvas_connection::CanvasConnectionRecord::new(
                        CanvasOrigin::parse(ORIGIN).expect("origin"),
                        CanvasAccessToken::new(ACCESS).expect("access"),
                    )
                    .serialize(),
                ),
            )
            .expect("store stale pat");
        assert_eq!(
            manager
                .resolve_canvas_default()
                .unwrap_err()
                .failure(),
            CanvasOAuthFailure::Missing
        );
    }

    #[test]
    fn development_falls_back_to_pat_only() {
        let http = Arc::new(FakeHttp::default());
        let (manager, _clock) = manager(RuntimeProfile::Development, http);
        crate::canvas_connection::configure_canvas_development_connection_in_store(
            RuntimeProfile::Development,
            &manager.store,
            ORIGIN,
            ACCESS,
        )
        .expect("configure dev PAT");
        let projected = manager.resolve_canvas_default().expect("resolve");
        let parsed = crate::canvas_connection::CanvasConnectionRecord::parse(
            projected.expose_for_trusted_native_use(),
        )
        .expect("projection");
        assert_eq!(
            parsed.origin().as_str(),
            ORIGIN
        );
    }

    #[test]
    fn concurrent_refresh_issues_exactly_one_request() {
        let http = Arc::new(FakeHttp::default());
        http.queue(
            200,
            &FakeHttp::token_body("synthetic-new-access", None, 3600),
        );
        let (manager, clock) = manager(RuntimeProfile::Development, http.clone());
        provisioned(&manager);
        let record = CanvasOAuthRecord::new(
            CanvasOrigin::parse(ORIGIN).expect("origin"),
            CLIENT_ID.to_string(),
            OAUTH_OOB_REDIRECT.to_string(),
            CanvasAccessToken::new(ACCESS).expect("access"),
            CanvasAccessToken::new(REFRESH).expect("refresh"),
            1_700_000_000,
            "Synthetic Student".to_string(),
        );
        manager
            .store
            .store_replace(
                CredentialSlot::CanvasOAuth,
                &SecretValue::new(record.serialize()),
            )
            .expect("store");
        clock.store(1_700_000_100, Ordering::SeqCst);

        let barrier = Arc::new(std::sync::Barrier::new(2));
        let mut handles = Vec::new();
        for _ in 0..2 {
            let manager = manager.clone();
            let barrier = barrier.clone();
            handles.push(std::thread::spawn(move || {
                barrier.wait();
                manager
                    .resolve_canvas_default()
                    .expect("resolve")
                    .expose_for_trusted_native_use()
                    .to_string()
            }));
        }
        let results: Vec<String> = handles
            .into_iter()
            .map(|handle| handle.join().expect("join"))
            .collect();
        assert_eq!(results[0], results[1]);
        assert_eq!(http.requests().len(), 1);
    }

    #[test]
    fn revoke_uses_delete_with_bearer_only() {
        let http = Arc::new(FakeHttp::default());
        http.queue(200, "");
        let (manager, _clock) = manager(RuntimeProfile::Development, http.clone());
        provisioned(&manager);
        let record = CanvasOAuthRecord::new(
            CanvasOrigin::parse(ORIGIN).expect("origin"),
            CLIENT_ID.to_string(),
            OAUTH_OOB_REDIRECT.to_string(),
            CanvasAccessToken::new(ACCESS).expect("access"),
            CanvasAccessToken::new(REFRESH).expect("refresh"),
            1_700_000_000,
            "Synthetic Student".to_string(),
        );
        manager
            .store
            .store_replace(
                CredentialSlot::CanvasOAuth,
                &SecretValue::new(record.serialize()),
            )
            .expect("store");
        manager.disconnect(true).expect("disconnect");
        let requests = http.requests();
        assert_eq!(requests.len(), 1);
        assert_eq!(requests[0].method, "DELETE");
        assert_eq!(requests[0].url, format!("{ORIGIN}{OAUTH_TOKEN_PATH}"));
        assert_eq!(
            requests[0].headers,
            vec![("Authorization".to_string(), format!("Bearer {ACCESS}"))]
        );
        assert!(requests[0].form.is_empty());
        assert!(manager.load_record(CredentialSlot::CanvasOAuth).is_err());
    }

    #[test]
    fn expired_renewable_record_stays_connected_and_refreshes_lazily() {
        let http = Arc::new(FakeHttp::default());
        http.queue(200, &FakeHttp::token_body("synthetic-new-access", None, 3600));
        let (manager, clock) = manager(RuntimeProfile::Development, http.clone());
        provisioned(&manager);
        let record = CanvasOAuthRecord::new(
            CanvasOrigin::parse(ORIGIN).expect("origin"),
            CLIENT_ID.to_string(),
            OAUTH_OOB_REDIRECT.to_string(),
            CanvasAccessToken::new(ACCESS).expect("access"),
            CanvasAccessToken::new(REFRESH).expect("refresh"),
            1_700_000_000,
            "Synthetic Student".to_string(),
        );
        manager
            .store
            .store_replace(
                CredentialSlot::CanvasOAuth,
                &SecretValue::new(record.serialize()),
            )
            .expect("store");
        clock.store(1_700_000_100, Ordering::SeqCst);
        assert_eq!(manager.status().state, CanvasConnectionState::Connected);
        let projected = manager.resolve_canvas_default().expect("resolve");
        let parsed = crate::canvas_connection::CanvasConnectionRecord::parse(
            projected.expose_for_trusted_native_use(),
        )
        .expect("projection");
        assert_eq!(
            parsed
                .token()
                .expose_for_authorized_canvas_request(),
            "synthetic-new-access"
        );
        assert_eq!(http.requests().len(), 1);
        assert_eq!(manager.status().state, CanvasConnectionState::Connected);
        let stored = manager.load_record(CredentialSlot::CanvasOAuth).expect("record");
        assert_eq!(
            stored.access_token().expect("access").expose_for_authorized_canvas_request(),
            "synthetic-new-access"
        );
    }

    #[test]
    fn state_expired_has_zero_exchange() {
        let http = Arc::new(FakeHttp::default());
        let (manager, clock) = manager(RuntimeProfile::Development, http.clone());
        provisioned(&manager);
        let start = manager.begin_oauth().expect("begin");
        let state = extract_state(&start.auth_url);
        clock.store(1_700_000_000 + 601, Ordering::SeqCst);
        assert_eq!(
            manager
                .complete_oauth(&response_url(&state, "synthetic-code"))
                .unwrap_err()
                .failure(),
            CanvasOAuthFailure::StateExpired
        );
        assert!(http.requests().is_empty());
        manager.begin_oauth().expect("fresh begin works");
    }

    #[test]
    fn authorization_token_contract_rejects_malformed() {
        let cases: Vec<&str> = vec![
            "{\"token_type\":\"Bearer\",\"expires_in\":3600}", // missing access
            "{\"access_token\":\"a\",\"expires_in\":3600}", // missing refresh
            "{\"access_token\":\"a\",\"refresh_token\":\"r\"}", // missing expiry
            "{\"access_token\":\"a\",\"refresh_token\":\"r\",\"expires_in\":0}", // expiry zero
            "{\"access_token\":\"a\",\"refresh_token\":\"r\",\"expires_in\":3600,\"token_type\":\"MAC\"}", // non-bearer
        ];
        for body in cases {
            assert!(parse_token_payload(body, true).is_err(), "body: {body}");
        }
    }

    #[test]
    fn refresh_token_contract_accepts_rotation_optional() {
        assert!(
            parse_token_payload(
                "{\"access_token\":\"a\",\"token_type\":\"Bearer\",\"expires_in\":3600}",
                false
            )
            .is_ok()
        );
        assert!(
            parse_token_payload(
                "{\"access_token\":\"a\",\"refresh_token\":\"r\",\"expires_in\":3600}",
                false
            )
            .is_ok()
        );
        assert!(
            parse_token_payload(
                "{\"access_token\":\"a\",\"expires_in\":0}",
                false
            )
            .is_err()
        );
        assert!(
            parse_token_payload(
                "{\"access_token\":\"a\",\"expires_in\":3600,\"token_type\":\"MAC\"}",
                false
            )
            .is_err()
        );
    }

    #[test]
    fn refresh_invalid_grant_persists_reconnect_across_restart() {
        let http = Arc::new(FakeHttp::default());
        http.queue(400, "{\"error\":\"invalid_grant\"}");
        let (manager, clock) = manager(RuntimeProfile::Development, http.clone());
        provisioned(&manager);
        let record = CanvasOAuthRecord::new(
            CanvasOrigin::parse(ORIGIN).expect("origin"),
            CLIENT_ID.to_string(),
            OAUTH_OOB_REDIRECT.to_string(),
            CanvasAccessToken::new(ACCESS).expect("access"),
            CanvasAccessToken::new(REFRESH).expect("refresh"),
            1_700_000_000,
            "Synthetic Student".to_string(),
        );
        manager
            .store
            .store_replace(
                CredentialSlot::CanvasOAuth,
                &SecretValue::new(record.serialize()),
            )
            .expect("store");
        clock.store(1_700_000_100, Ordering::SeqCst);
        assert_eq!(
            manager
                .resolve_canvas_default()
                .unwrap_err()
                .failure(),
            CanvasOAuthFailure::ReconnectRequired
        );
        assert_eq!(http.requests().len(), 1);
        let stored = manager.load_record(CredentialSlot::CanvasOAuth).expect("record");
        assert!(stored.needs_reconnect);
        assert!(stored.access_token().is_none());
        assert!(stored.refresh_token().is_none());

        let second = CanvasCredentialManager::new(
            RuntimeProfile::Development,
            manager.store.clone(),
            http.clone(),
            manager.now.clone(),
        );
        assert_eq!(
            second.status().state,
            CanvasConnectionState::ReconnectRequired
        );
        assert_eq!(
            second
                .resolve_canvas_default()
                .unwrap_err()
                .failure(),
            CanvasOAuthFailure::ReconnectRequired
        );
        assert_eq!(http.requests().len(), 1);
    }

    #[test]
    fn transient_refresh_failures_preserve_record() {
        for (status, body) in [(429u16, "{}"), (500, "{}")] {
            let http = Arc::new(FakeHttp::default());
            http.queue(status, body);
            let (manager, clock) = manager(RuntimeProfile::Development, http.clone());
            provisioned(&manager);
            let record = CanvasOAuthRecord::new(
                CanvasOrigin::parse(ORIGIN).expect("origin"),
                CLIENT_ID.to_string(),
                OAUTH_OOB_REDIRECT.to_string(),
                CanvasAccessToken::new(ACCESS).expect("access"),
                CanvasAccessToken::new(REFRESH).expect("refresh"),
                1_700_000_000,
                "Synthetic Student".to_string(),
            );
            manager
                .store
                .store_replace(
                    CredentialSlot::CanvasOAuth,
                    &SecretValue::new(record.serialize()),
                )
                .expect("store");
            clock.store(1_700_000_100, Ordering::SeqCst);
            let error = manager.resolve_canvas_default().unwrap_err();
            assert!(!matches!(
                error.failure(),
                CanvasOAuthFailure::ReconnectRequired
            ));
            let stored = manager.load_record(CredentialSlot::CanvasOAuth).expect("record");
            assert!(!stored.needs_reconnect);
            assert!(stored.access_token().is_some());
            assert!(stored.refresh_token().is_some());
            assert_eq!(manager.status().state, CanvasConnectionState::Connected);
        }
    }

    #[test]
    fn transport_failure_preserves_record() {
        let http = Arc::new(FakeHttp::default());
        let (manager, clock) = manager(RuntimeProfile::Development, http.clone());
        provisioned(&manager);
        let record = CanvasOAuthRecord::new(
            CanvasOrigin::parse(ORIGIN).expect("origin"),
            CLIENT_ID.to_string(),
            OAUTH_OOB_REDIRECT.to_string(),
            CanvasAccessToken::new(ACCESS).expect("access"),
            CanvasAccessToken::new(REFRESH).expect("refresh"),
            1_700_000_000,
            "Synthetic Student".to_string(),
        );
        manager
            .store
            .store_replace(
                CredentialSlot::CanvasOAuth,
                &SecretValue::new(record.serialize()),
            )
            .expect("store");
        clock.store(1_700_000_100, Ordering::SeqCst);
        let error = manager.resolve_canvas_default().unwrap_err();
        assert_eq!(error.failure(), CanvasOAuthFailure::TransportFailure);
        let stored = manager.load_record(CredentialSlot::CanvasOAuth).expect("record");
        assert!(!stored.needs_reconnect);
        assert!(stored.refresh_token().is_some());
    }

    #[test]
    fn oauth_client_binding_mismatch_fails_before_network() {
        let http = Arc::new(FakeHttp::default());
        let (manager, clock) = manager(RuntimeProfile::Development, http.clone());
        provisioned(&manager);
        let record = CanvasOAuthRecord::new(
            CanvasOrigin::parse("https://canvas.school-a.example").expect("origin"),
            "client-A".to_string(),
            OAUTH_OOB_REDIRECT.to_string(),
            CanvasAccessToken::new(ACCESS).expect("access"),
            CanvasAccessToken::new(REFRESH).expect("refresh"),
            1_700_000_000,
            "Synthetic Student".to_string(),
        );
        manager
            .store
            .store_replace(
                CredentialSlot::CanvasOAuth,
                &SecretValue::new(record.serialize()),
            )
            .expect("store");
        clock.store(1_700_000_100, Ordering::SeqCst);
        manager
            .provision_client_config(
                "https://canvas.school-b.example",
                "client-B",
                CLIENT_SECRET,
                OAUTH_OOB_REDIRECT,
            )
            .expect("reprovision origin B");
        assert_eq!(
            manager
                .resolve_canvas_default()
                .unwrap_err()
                .failure(),
            CanvasOAuthFailure::ClientBindingMismatch
        );
        assert!(http.requests().is_empty());
    }

    #[test]
    fn oauth_client_binding_client_id_mismatch_fails_before_network() {
        let http = Arc::new(FakeHttp::default());
        let (manager, clock) = manager(RuntimeProfile::Development, http.clone());
        provisioned(&manager);
        let record = CanvasOAuthRecord::new(
            CanvasOrigin::parse(ORIGIN).expect("origin"),
            "client-A".to_string(),
            OAUTH_OOB_REDIRECT.to_string(),
            CanvasAccessToken::new(ACCESS).expect("access"),
            CanvasAccessToken::new(REFRESH).expect("refresh"),
            1_700_000_000,
            "Synthetic Student".to_string(),
        );
        manager
            .store
            .store_replace(
                CredentialSlot::CanvasOAuth,
                &SecretValue::new(record.serialize()),
            )
            .expect("store");
        clock.store(1_700_000_100, Ordering::SeqCst);
        manager
            .provision_client_config(ORIGIN, "client-B", CLIENT_SECRET, OAUTH_OOB_REDIRECT)
            .expect("reprovision client B");
        assert_eq!(
            manager
                .resolve_canvas_default()
                .unwrap_err()
                .failure(),
            CanvasOAuthFailure::ClientBindingMismatch
        );
        assert!(http.requests().is_empty());
    }

    #[test]
    fn oauth_request_debug_redacts_all_canaries() {
        let request = CanvasOAuthRequest {
            method: "POST",
            url: format!("{ORIGIN}{OAUTH_TOKEN_PATH}"),
            headers: vec![(
                "Authorization".to_string(),
                format!("Bearer {}", "SYN_CANVAS_ACCESS_TOKEN_DEBUG"),
            )],
            form: vec![
                ("client_secret".to_string(), "SYN_CANVAS_CLIENT_SECRET_DEBUG".to_string()),
                ("refresh_token".to_string(), "SYN_CANVAS_REFRESH_TOKEN_DEBUG".to_string()),
                ("code".to_string(), "SYN_CANVAS_AUTH_CODE_DEBUG".to_string()),
                ("grant_type".to_string(), "authorization_code".to_string()),
            ],
        };
        let rendered = format!("{request:?}");
        for canary in [
            "SYN_CANVAS_ACCESS_TOKEN_DEBUG",
            "SYN_CANVAS_REFRESH_TOKEN_DEBUG",
            "SYN_CANVAS_CLIENT_SECRET_DEBUG",
            "SYN_CANVAS_AUTH_CODE_DEBUG",
        ] {
            assert!(!rendered.contains(canary), "leaked {canary} in {rendered}");
        }
        assert!(rendered.contains("client_secret=<redacted>"));
        assert!(rendered.contains("Authorization: <redacted>"));
        assert!(rendered.contains("grant_type=authorization_code"));
    }

    #[test]
    fn refresh_reuses_existing_refresh_token_when_not_rotated() {
        let http = Arc::new(FakeHttp::default());
        http.queue(200, &FakeHttp::token_body("synthetic-new-access", None, 3600));
        let (manager, clock) = manager(RuntimeProfile::Development, http.clone());
        provisioned(&manager);
        let record = CanvasOAuthRecord::new(
            CanvasOrigin::parse(ORIGIN).expect("origin"),
            CLIENT_ID.to_string(),
            OAUTH_OOB_REDIRECT.to_string(),
            CanvasAccessToken::new(ACCESS).expect("access"),
            CanvasAccessToken::new(REFRESH).expect("refresh"),
            1_700_000_000,
            "Synthetic Student".to_string(),
        );
        manager
            .store
            .store_replace(
                CredentialSlot::CanvasOAuth,
                &SecretValue::new(record.serialize()),
            )
            .expect("store");
        clock.store(1_700_000_100, Ordering::SeqCst);
        manager.resolve_canvas_default().expect("resolve");
        let stored = manager.load_record(CredentialSlot::CanvasOAuth).expect("record");
        assert_eq!(
            stored
                .refresh_token()
                .expect("refresh")
                .expose_for_authorized_canvas_request(),
            REFRESH
        );
    }
}
