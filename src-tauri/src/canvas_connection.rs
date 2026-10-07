#![allow(dead_code)]

use std::fmt;
use std::net::IpAddr;

use tauri::Url;

use crate::credential_store::{
    CredentialAvailability, CredentialSlot, CredentialStore, CredentialStoreError,
    CredentialStoreFailure, SecretValue,
};

const RECORD_PREFIX: &str = "studyhub-canvas-connection-v1";
const MAX_ORIGIN_BYTES: usize = 512;
const MAX_TOKEN_BYTES: usize = 4096;
const CANVAS_TOKEN_FIELD: &str = "access_token";

#[derive(Clone, Eq, PartialEq)]
pub(crate) struct CanvasAccessToken(String);

impl CanvasAccessToken {
    pub(crate) fn new(value: impl Into<String>) -> Result<Self, CanvasConnectionError> {
        let value = value.into();
        let trimmed = value.trim();
        if trimmed.is_empty() || trimmed.len() > MAX_TOKEN_BYTES {
            return Err(CanvasConnectionError::new(CanvasConnectionFailure::InvalidToken));
        }
        if trimmed.chars().any(|ch| ch.is_control() || ch.is_whitespace()) {
            return Err(CanvasConnectionError::new(CanvasConnectionFailure::InvalidToken));
        }
        Ok(Self(trimmed.to_string()))
    }

    pub(crate) fn expose_for_authorized_canvas_request(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for CanvasAccessToken {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("CanvasAccessToken(<redacted>)")
    }
}

#[derive(Clone, Eq, PartialEq)]
pub(crate) struct CanvasOrigin(String);

impl CanvasOrigin {
    pub(crate) fn parse(raw: &str) -> Result<Self, CanvasConnectionError> {
        if raw.to_ascii_lowercase().contains(CANVAS_TOKEN_FIELD) {
            return Err(CanvasConnectionError::new(CanvasConnectionFailure::InvalidOrigin));
        }
        let parsed =
            Url::parse(raw.trim()).map_err(|_| CanvasConnectionError::new(CanvasConnectionFailure::InvalidOrigin))?;
        if parsed.scheme() != "https" {
            return Err(CanvasConnectionError::new(CanvasConnectionFailure::InvalidOrigin));
        }
        if !parsed.username().is_empty() || parsed.password().is_some() {
            return Err(CanvasConnectionError::new(CanvasConnectionFailure::InvalidOrigin));
        }
        if parsed.query().is_some() || parsed.fragment().is_some() {
            return Err(CanvasConnectionError::new(CanvasConnectionFailure::InvalidOrigin));
        }
        if !matches!(parsed.path(), "" | "/") {
            return Err(CanvasConnectionError::new(CanvasConnectionFailure::InvalidOrigin));
        }
        let host = parsed
            .host_str()
            .ok_or_else(|| CanvasConnectionError::new(CanvasConnectionFailure::InvalidOrigin))?;
        if host.parse::<IpAddr>().is_ok()
            || host
                .trim_start_matches('[')
                .trim_end_matches(']')
                .parse::<IpAddr>()
                .is_ok()
        {
            return Err(CanvasConnectionError::new(CanvasConnectionFailure::InvalidOrigin));
        }
        let normalized_host = host.to_ascii_lowercase();
        let origin = if let Some(port) = parsed.port() {
            format!("https://{normalized_host}:{port}")
        } else {
            format!("https://{normalized_host}")
        };
        if origin.len() > MAX_ORIGIN_BYTES {
            return Err(CanvasConnectionError::new(CanvasConnectionFailure::InvalidOrigin));
        }
        Ok(Self(origin))
    }

    pub(crate) fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for CanvasOrigin {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.debug_tuple("CanvasOrigin").field(&self.0).finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub(crate) struct CanvasConnectionRecord {
    origin: CanvasOrigin,
    token: CanvasAccessToken,
}

impl CanvasConnectionRecord {
    pub(crate) fn new(origin: CanvasOrigin, token: CanvasAccessToken) -> Self {
        Self { origin, token }
    }

    pub(crate) fn parse(serialized: &str) -> Result<Self, CanvasConnectionError> {
        let mut lines = serialized.lines();
        if lines.next() != Some(RECORD_PREFIX) {
            return Err(CanvasConnectionError::new(
                CanvasConnectionFailure::MalformedRecord,
            ));
        }
        let origin_hex = lines
            .next()
            .and_then(|line| line.strip_prefix("origin_hex="))
            .ok_or_else(|| CanvasConnectionError::new(CanvasConnectionFailure::MalformedRecord))?;
        let token_hex = lines
            .next()
            .and_then(|line| line.strip_prefix("token_hex="))
            .ok_or_else(|| CanvasConnectionError::new(CanvasConnectionFailure::MalformedRecord))?;
        if lines.next().is_some() {
            return Err(CanvasConnectionError::new(
                CanvasConnectionFailure::MalformedRecord,
            ));
        }
        let origin = decode_hex_to_string(origin_hex, MAX_ORIGIN_BYTES)
            .and_then(|value| CanvasOrigin::parse(&value))?;
        let decoded_secret = decode_hex_to_string(token_hex, MAX_TOKEN_BYTES)
            .and_then(CanvasAccessToken::new)?;
        Ok(Self {
            origin,
            token: decoded_secret,
        })
    }

    pub(crate) fn serialize(&self) -> String {
        format!(
            "{RECORD_PREFIX}\norigin_hex={}\ntoken_hex={}",
            encode_hex(self.origin.as_str().as_bytes()),
            encode_hex(self.token.expose_for_authorized_canvas_request().as_bytes())
        )
    }

    pub(crate) fn origin(&self) -> &CanvasOrigin {
        &self.origin
    }

    pub(crate) fn token(&self) -> &CanvasAccessToken {
        &self.token
    }
}

impl fmt::Debug for CanvasConnectionRecord {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CanvasConnectionRecord")
            .field("origin", &self.origin)
            .field("token", &"<redacted>")
            .finish()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum CanvasConnectionFailure {
    InvalidOrigin,
    InvalidToken,
    MalformedRecord,
    Missing,
    BackendUnavailable,
    AccessDenied,
    OperationFailed,
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub(crate) struct CanvasConnectionError {
    failure: CanvasConnectionFailure,
}

impl CanvasConnectionError {
    pub(crate) fn new(failure: CanvasConnectionFailure) -> Self {
        Self { failure }
    }

    pub(crate) fn failure(self) -> CanvasConnectionFailure {
        self.failure
    }
}

impl fmt::Debug for CanvasConnectionError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CanvasConnectionError")
            .field("failure", &self.failure)
            .finish()
    }
}

impl fmt::Display for CanvasConnectionError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self.failure {
            CanvasConnectionFailure::InvalidOrigin => "canvas_invalid_origin",
            CanvasConnectionFailure::InvalidToken => "canvas_invalid_token",
            CanvasConnectionFailure::MalformedRecord => "canvas_malformed_connection_record",
            CanvasConnectionFailure::Missing => "canvas_connection_missing",
            CanvasConnectionFailure::BackendUnavailable => "credential_backend_unavailable",
            CanvasConnectionFailure::AccessDenied => "credential_access_denied",
            CanvasConnectionFailure::OperationFailed => "credential_operation_failed",
        })
    }
}

impl std::error::Error for CanvasConnectionError {}

pub(crate) fn configure_canvas_connection_in_store(
    store: &impl CredentialStore,
    origin: &str,
    access_token: &str,
) -> Result<(), CanvasConnectionError> {
    let record = CanvasConnectionRecord::new(
        CanvasOrigin::parse(origin)?,
        CanvasAccessToken::new(access_token)?,
    );
    store
        .store_replace(
            CredentialSlot::CanvasDefault,
            &SecretValue::new(record.serialize()),
        )
        .map_err(map_store_error)
}

pub(crate) fn canvas_connection_status_in_store(
    store: &impl CredentialStore,
) -> Result<CredentialAvailability, CanvasConnectionError> {
    store.exists(CredentialSlot::CanvasDefault).map_err(map_store_error)
}

pub(crate) fn remove_canvas_connection_in_store(
    store: &impl CredentialStore,
) -> Result<(), CanvasConnectionError> {
    match store.delete(CredentialSlot::CanvasDefault) {
        Ok(()) => Ok(()),
        Err(error) if error.failure() == CredentialStoreFailure::Missing => Ok(()),
        Err(error) => Err(map_store_error(error)),
    }
}

fn map_store_error(error: CredentialStoreError) -> CanvasConnectionError {
    let failure = match error.failure() {
        CredentialStoreFailure::Missing => CanvasConnectionFailure::Missing,
        CredentialStoreFailure::BackendUnavailable => CanvasConnectionFailure::BackendUnavailable,
        CredentialStoreFailure::AccessDenied => CanvasConnectionFailure::AccessDenied,
        CredentialStoreFailure::OperationFailed => CanvasConnectionFailure::OperationFailed,
    };
    CanvasConnectionError::new(failure)
}

fn encode_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}

fn decode_hex_to_string(
    encoded: &str,
    max_decoded_bytes: usize,
) -> Result<String, CanvasConnectionError> {
    if encoded.len() > max_decoded_bytes * 2 || encoded.len() % 2 != 0 {
        return Err(CanvasConnectionError::new(
            CanvasConnectionFailure::MalformedRecord,
        ));
    }
    let mut bytes = Vec::with_capacity(encoded.len() / 2);
    for pair in encoded.as_bytes().chunks_exact(2) {
        let high = decode_hex_nibble(pair[0])?;
        let low = decode_hex_nibble(pair[1])?;
        bytes.push((high << 4) | low);
    }
    String::from_utf8(bytes)
        .map_err(|_| CanvasConnectionError::new(CanvasConnectionFailure::MalformedRecord))
}

fn decode_hex_nibble(byte: u8) -> Result<u8, CanvasConnectionError> {
    match byte {
        b'0'..=b'9' => Ok(byte - b'0'),
        b'a'..=b'f' => Ok(byte - b'a' + 10),
        b'A'..=b'F' => Ok(byte - b'A' + 10),
        _ => Err(CanvasConnectionError::new(
            CanvasConnectionFailure::MalformedRecord,
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::credential_store::{CredentialStore, MemoryCredentialStore, namespace_for_profile};
    use crate::RuntimeProfile;

    const TOKEN: &str = "syn-token";
    const SECRET_CANARY: &str = "syn-canary";

    #[test]
    fn canvas_origin_validation_accepts_https_origin_and_normalizes() {
        let origin = CanvasOrigin::parse("https://Canvas.Example.Edu/").expect("origin");
        assert_eq!(origin.as_str(), "https://canvas.example.edu");
        let with_port = CanvasOrigin::parse("https://canvas.example.edu:8443").expect("origin");
        assert_eq!(with_port.as_str(), "https://canvas.example.edu:8443");
    }

    #[test]
    fn canvas_origin_validation_rejects_unsafe_inputs() {
        for value in [
            "http://canvas.example.edu",
            concat!("https://user:pass", "@canvas.example.edu"),
            "https://canvas.example.edu?access_token=abc",
            "https://canvas.example.edu/#fragment",
            "https://canvas.example.edu/api/v1/courses",
            "https://127.0.0.1",
            "https://[::1]",
        ] {
            assert_eq!(
                CanvasOrigin::parse(value).unwrap_err().failure(),
                CanvasConnectionFailure::InvalidOrigin
            );
        }
    }

    #[test]
    fn canvas_connection_record_round_trips_and_redacts_token() {
        let record = CanvasConnectionRecord::new(
            CanvasOrigin::parse("https://canvas.example.edu").expect("origin"),
            CanvasAccessToken::new(SECRET_CANARY).expect("token"),
        );
        let serialized = record.serialize();
        assert!(serialized.starts_with(RECORD_PREFIX));
        assert!(!format!("{record:?}").contains(SECRET_CANARY));
        assert!(!format!("{:?}", CanvasAccessToken::new(SECRET_CANARY).unwrap())
            .contains(SECRET_CANARY));

        let parsed = CanvasConnectionRecord::parse(&serialized).expect("parse");
        assert_eq!(parsed.origin().as_str(), "https://canvas.example.edu");
        assert_eq!(
            parsed.token().expose_for_authorized_canvas_request(),
            SECRET_CANARY
        );
    }

    #[test]
    fn malformed_connection_records_fail_closed() {
        for value in [
            "",
            "studyhub-canvas-connection-v2\norigin_hex=00\ntoken_hex=00",
            "studyhub-canvas-connection-v1\norigin_hex=zz\ntoken_hex=00",
        ] {
            assert_eq!(
                CanvasConnectionRecord::parse(value).unwrap_err().failure(),
                CanvasConnectionFailure::MalformedRecord
            );
        }
        assert_eq!(
            CanvasConnectionRecord::parse(
                "studyhub-canvas-connection-v1\norigin_hex=687474703a2f2f6578616d706c652e656475\ntoken_hex=616263"
            )
            .unwrap_err()
            .failure(),
            CanvasConnectionFailure::InvalidOrigin
        );
    }

    #[test]
    fn fake_store_covers_configure_status_replace_and_delete() {
        let store = MemoryCredentialStore::new(namespace_for_profile(RuntimeProfile::Development));
        assert_eq!(
            canvas_connection_status_in_store(&store).expect("status"),
            CredentialAvailability::Missing
        );
        configure_canvas_connection_in_store(&store, "https://canvas.example.edu", TOKEN)
            .expect("configure");
        assert_eq!(
            canvas_connection_status_in_store(&store).expect("status"),
            CredentialAvailability::Configured
        );
        configure_canvas_connection_in_store(
            &store,
            "https://canvas-alt.example.edu",
            "synthetic-replacement-token",
        )
        .expect("replace");
        let stored = store
            .get_for_trusted_native_use(CredentialSlot::CanvasDefault)
            .expect("stored");
        let parsed = CanvasConnectionRecord::parse(stored.expose_for_trusted_native_use())
            .expect("record");
        assert_eq!(parsed.origin().as_str(), "https://canvas-alt.example.edu");
        remove_canvas_connection_in_store(&store).expect("delete");
        assert_eq!(
            canvas_connection_status_in_store(&store).expect("status"),
            CredentialAvailability::Missing
        );
    }

    #[test]
    fn profile_namespaces_keep_canvas_records_isolated() {
        let production = MemoryCredentialStore::new(namespace_for_profile(RuntimeProfile::Production));
        let development =
            MemoryCredentialStore::new(namespace_for_profile(RuntimeProfile::Development));
        configure_canvas_connection_in_store(&production, "https://canvas.example.edu", TOKEN)
            .expect("configure");
        assert_eq!(
            canvas_connection_status_in_store(&production).expect("status"),
            CredentialAvailability::Configured
        );
        assert_eq!(
            canvas_connection_status_in_store(&development).expect("status"),
            CredentialAvailability::Missing
        );
    }

    #[test]
    fn demo_test_denial_remains_outside_native_storage() {
        let denied = crate::credential_store::credential_store_for_profile(RuntimeProfile::DemoTest);
        assert_eq!(
            configure_canvas_connection_in_store(&denied, "https://canvas.example.edu", TOKEN)
                .unwrap_err()
                .failure(),
            CanvasConnectionFailure::BackendUnavailable
        );
    }
}
