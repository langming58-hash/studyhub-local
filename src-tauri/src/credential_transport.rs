#![allow(dead_code)]

use std::fmt;
use std::io::{self, Read, Write};
use std::process::Command;
use std::thread;

#[cfg(unix)]
use std::os::fd::{AsRawFd, RawFd};
#[cfg(unix)]
use std::os::unix::net::UnixStream;
#[cfg(unix)]
use std::os::unix::process::CommandExt;

use crate::credential_handoff::{
    CredentialHandoffBroker, HandoffError, HandoffFailure, HandoffRequest,
};
use crate::credential_store::{credential_store_for_profile, CredentialStore};
use crate::RuntimeProfile;

pub(crate) const TRANSPORT_FD_ENV: &str = "STUDYHUB_CREDENTIAL_TRANSPORT_FD";
pub(crate) const TRANSPORT_PROTOCOL_ENV: &str = "STUDYHUB_CREDENTIAL_PROTOCOL";
pub(crate) const TRANSPORT_KIND_ENV: &str = "STUDYHUB_CREDENTIAL_TRANSPORT";
const PROTOCOL: &str = "studyhub-credential/1";
const MAX_FRAME_BYTES: usize = 8192;
#[cfg(unix)]
const CHILD_TRANSPORT_FD: RawFd = 3;

#[derive(Clone, Copy, Eq, PartialEq)]
pub(crate) struct LiveSessionAuthority {
    bytes: [u8; 32],
}

impl LiveSessionAuthority {
    #[cfg(unix)]
    pub(crate) fn new_os_random() -> io::Result<Self> {
        let mut bytes = [0u8; 32];
        std::fs::File::open("/dev/urandom")?.read_exact(&mut bytes)?;
        Ok(Self { bytes })
    }

    #[cfg(test)]
    fn new_for_test(byte: u8) -> Self {
        Self { bytes: [byte; 32] }
    }

    fn as_hex(self) -> String {
        encode_hex(&self.bytes)
    }
}

impl fmt::Debug for LiveSessionAuthority {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("LiveSessionAuthority(<redacted>)")
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum TransportFailure {
    TransportUnavailable,
    UnauthorizedOrStaleSession,
    MalformedRequest,
    UnsupportedProtocol,
    UnsupportedOperation,
    OversizedFrame,
    TruncatedFrame,
    CredentialMissing,
    CredentialBackendUnavailable,
    CredentialAccessDenied,
    CredentialOperationFailed,
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub(crate) struct TransportError {
    failure: TransportFailure,
}

impl TransportError {
    fn new(failure: TransportFailure) -> Self {
        Self { failure }
    }

    pub(crate) fn failure(self) -> TransportFailure {
        self.failure
    }
}

impl fmt::Debug for TransportError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("TransportError")
            .field("failure", &self.failure)
            .finish()
    }
}

impl fmt::Display for TransportError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(error_code(self.failure))
    }
}

impl std::error::Error for TransportError {}

#[cfg(unix)]
pub(crate) struct PendingCredentialTransport {
    parent_stream: UnixStream,
    _child_stream: UnixStream,
}

#[cfg(unix)]
impl PendingCredentialTransport {
    fn into_parent_stream(self) -> UnixStream {
        self.parent_stream
    }
}

#[cfg(unix)]
pub(crate) fn configure_child_transport(
    command: &mut Command,
) -> io::Result<PendingCredentialTransport> {
    let (parent_stream, child_stream) = UnixStream::pair()?;
    set_cloexec(parent_stream.as_raw_fd(), true)?;
    set_cloexec(child_stream.as_raw_fd(), true)?;
    let child_fd = child_stream.as_raw_fd();
    command
        .env(TRANSPORT_FD_ENV, CHILD_TRANSPORT_FD.to_string())
        .env(TRANSPORT_PROTOCOL_ENV, "1")
        .env(TRANSPORT_KIND_ENV, "unix-fd");

    unsafe {
        command.pre_exec(move || {
            if child_fd != CHILD_TRANSPORT_FD {
                if libc::dup2(child_fd, CHILD_TRANSPORT_FD) == -1 {
                    return Err(io::Error::last_os_error());
                }
                libc::close(child_fd);
            }
            set_cloexec(CHILD_TRANSPORT_FD, false)
        });
    }

    Ok(PendingCredentialTransport {
        parent_stream,
        _child_stream: child_stream,
    })
}

#[cfg(unix)]
fn set_cloexec(fd: RawFd, enabled: bool) -> io::Result<()> {
    unsafe {
        let flags = libc::fcntl(fd, libc::F_GETFD);
        if flags == -1 {
            return Err(io::Error::last_os_error());
        }
        let next = if enabled {
            flags | libc::FD_CLOEXEC
        } else {
            flags & !libc::FD_CLOEXEC
        };
        if libc::fcntl(fd, libc::F_SETFD, next) == -1 {
            return Err(io::Error::last_os_error());
        }
    }
    Ok(())
}

#[cfg(unix)]
pub(crate) fn spawn_backend_credential_transport(
    pending: PendingCredentialTransport,
    child_id: u32,
    profile: RuntimeProfile,
) {
    thread::spawn(move || {
        let stream = pending.into_parent_stream();
        let store = credential_store_for_profile(profile);
        let broker = CredentialHandoffBroker::new(store);
        let _ = serve_backend_credential_transport(stream, broker, child_id);
    });
}

#[cfg(not(unix))]
pub(crate) fn configure_child_transport(_command: &mut Command) -> io::Result<()> {
    Ok(())
}

pub(crate) fn serve_backend_credential_transport<S, IO>(
    mut stream: IO,
    mut broker: CredentialHandoffBroker<S>,
    child_id: u32,
) -> Result<(), TransportError>
where
    S: CredentialStore,
    IO: Read + Write,
{
    let authority = LiveSessionAuthority::new_os_random()
        .map_err(|_| TransportError::new(TransportFailure::TransportUnavailable))?;
    serve_backend_credential_transport_with_session(&mut stream, &mut broker, child_id, authority)
}

fn serve_backend_credential_transport_with_session<S, IO>(
    stream: &mut IO,
    broker: &mut CredentialHandoffBroker<S>,
    child_id: u32,
    authority: LiveSessionAuthority,
) -> Result<(), TransportError>
where
    S: CredentialStore,
    IO: Read + Write,
{
    let child = broker.authorize_backend_child(child_id);
    let session_hex = authority.as_hex();
    write_frame(stream, &format!("{PROTOCOL} session={session_hex}"))?;
    loop {
        let request = match read_frame(stream) {
            Ok(payload) => payload,
            Err(error) if error.failure() == TransportFailure::TruncatedFrame => return Ok(()),
            Err(error) => {
                let _ = write_error(stream, error.failure());
                return Err(error);
            }
        };
        let response = match handle_request(broker, child, &session_hex, &request) {
            Ok(secret) => format!(
                "{PROTOCOL} ok credential_hex={}",
                encode_hex(secret.as_bytes())
            ),
            Err(error) => format!("{PROTOCOL} err code={}", error_code(error.failure())),
        };
        write_frame(stream, &response)?;
    }
}

fn handle_request<S: CredentialStore>(
    broker: &CredentialHandoffBroker<S>,
    child: crate::credential_handoff::BackendChildAuthorization,
    session_hex: &str,
    request: &str,
) -> Result<String, TransportError> {
    let parts: Vec<&str> = request.split_whitespace().collect();
    if parts.is_empty() {
        return Err(TransportError::new(TransportFailure::MalformedRequest));
    }
    if parts[0] != PROTOCOL {
        return if parts[0].starts_with("studyhub-credential/") {
            Err(TransportError::new(TransportFailure::UnsupportedProtocol))
        } else {
            Err(TransportError::new(TransportFailure::MalformedRequest))
        };
    }
    if parts.len() != 4 || parts[1] != "use" || !parts[3].starts_with("session=") {
        return Err(TransportError::new(TransportFailure::MalformedRequest));
    }
    if parts[3].strip_prefix("session=") != Some(session_hex) {
        return Err(TransportError::new(
            TransportFailure::UnauthorizedOrStaleSession,
        ));
    }
    let request = match parts[2] {
        "canvas_default" => HandoffRequest::UseCanvasDefault,
        _ => return Err(TransportError::new(TransportFailure::UnsupportedOperation)),
    };
    broker
        .with_credential_for_authorized_child(child, request, |secret| {
            secret.expose_for_trusted_native_use().to_string()
        })
        .map_err(map_handoff_error)
}

fn read_frame(stream: &mut impl Read) -> Result<String, TransportError> {
    let mut len_buf = [0u8; 4];
    if let Err(error) = stream.read_exact(&mut len_buf) {
        return if error.kind() == io::ErrorKind::UnexpectedEof {
            Err(TransportError::new(TransportFailure::TruncatedFrame))
        } else {
            Err(TransportError::new(TransportFailure::TransportUnavailable))
        };
    }
    let len = u32::from_be_bytes(len_buf) as usize;
    if len > MAX_FRAME_BYTES {
        return Err(TransportError::new(TransportFailure::OversizedFrame));
    }
    let mut payload = vec![0u8; len];
    stream
        .read_exact(&mut payload)
        .map_err(|_| TransportError::new(TransportFailure::TruncatedFrame))?;
    String::from_utf8(payload).map_err(|_| TransportError::new(TransportFailure::MalformedRequest))
}

fn write_frame(stream: &mut impl Write, payload: &str) -> Result<(), TransportError> {
    if payload.len() > MAX_FRAME_BYTES {
        return Err(TransportError::new(TransportFailure::OversizedFrame));
    }
    stream
        .write_all(&(payload.len() as u32).to_be_bytes())
        .and_then(|_| stream.write_all(payload.as_bytes()))
        .and_then(|_| stream.flush())
        .map_err(|_| TransportError::new(TransportFailure::TransportUnavailable))
}

fn write_error(stream: &mut impl Write, failure: TransportFailure) -> Result<(), TransportError> {
    write_frame(
        stream,
        &format!("{PROTOCOL} err code={}", error_code(failure)),
    )
}

fn map_handoff_error(error: HandoffError) -> TransportError {
    let failure = match error.failure() {
        HandoffFailure::UnauthorizedChild | HandoffFailure::StaleAuthorization => {
            TransportFailure::UnauthorizedOrStaleSession
        }
        HandoffFailure::SlotNotAllowed => TransportFailure::UnsupportedOperation,
        HandoffFailure::MalformedRequest => TransportFailure::MalformedRequest,
        HandoffFailure::MissingCredential => TransportFailure::CredentialMissing,
        HandoffFailure::BackendUnavailable => TransportFailure::CredentialBackendUnavailable,
        HandoffFailure::AccessDenied => TransportFailure::CredentialAccessDenied,
        HandoffFailure::OperationFailed => TransportFailure::CredentialOperationFailed,
    };
    TransportError::new(failure)
}

fn error_code(failure: TransportFailure) -> &'static str {
    match failure {
        TransportFailure::TransportUnavailable => "transport_unavailable",
        TransportFailure::UnauthorizedOrStaleSession => "unauthorized_or_stale_session",
        TransportFailure::MalformedRequest => "malformed_request",
        TransportFailure::UnsupportedProtocol => "unsupported_protocol",
        TransportFailure::UnsupportedOperation => "unsupported_operation",
        TransportFailure::OversizedFrame => "oversized_frame",
        TransportFailure::TruncatedFrame => "truncated_frame",
        TransportFailure::CredentialMissing => "credential_missing",
        TransportFailure::CredentialBackendUnavailable => "credential_backend_unavailable",
        TransportFailure::CredentialAccessDenied => "credential_access_denied",
        TransportFailure::CredentialOperationFailed => "credential_operation_failed",
    }
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

#[cfg(test)]
mod tests {
    use std::collections::HashMap;
    use std::io::{Cursor, Read, Write};
    use std::sync::{Arc, Mutex};

    use super::*;
    use crate::credential_store::{
        CredentialAvailability, CredentialSlot, CredentialStoreError, CredentialStoreFailure,
        CredentialStoreResult, SecretValue,
    };

    const SAMPLE_VALUE: &str = "synthetic-live-transport-value";

    #[derive(Clone, Default)]
    struct SharedStore {
        values: Arc<Mutex<HashMap<CredentialSlot, SecretValue>>>,
        failures: Arc<Mutex<Vec<CredentialStoreFailure>>>,
    }

    impl SharedStore {
        fn with_sample_value() -> Self {
            let store = Self::default();
            store
                .store_replace(
                    CredentialSlot::CanvasDefault,
                    &SecretValue::new(SAMPLE_VALUE),
                )
                .expect("synthetic store");
            store
        }

        fn fail_next(&self, failure: CredentialStoreFailure) {
            self.failures.lock().expect("test lock").push(failure);
        }
    }

    impl CredentialStore for SharedStore {
        fn store_replace(
            &self,
            slot: CredentialSlot,
            secret: &SecretValue,
        ) -> CredentialStoreResult<()> {
            self.values
                .lock()
                .expect("test lock")
                .insert(slot, secret.clone());
            Ok(())
        }

        fn exists(&self, slot: CredentialSlot) -> CredentialStoreResult<CredentialAvailability> {
            let exists = self.values.lock().expect("test lock").contains_key(&slot);
            Ok(if exists {
                CredentialAvailability::Configured
            } else {
                CredentialAvailability::Missing
            })
        }

        fn get_for_trusted_native_use(
            &self,
            slot: CredentialSlot,
        ) -> CredentialStoreResult<SecretValue> {
            if let Some(failure) = self.failures.lock().expect("test lock").pop() {
                return Err(CredentialStoreError::new(failure));
            }
            self.values
                .lock()
                .expect("test lock")
                .get(&slot)
                .cloned()
                .ok_or_else(|| CredentialStoreError::new(CredentialStoreFailure::Missing))
        }

        fn delete(&self, slot: CredentialSlot) -> CredentialStoreResult<()> {
            self.values.lock().expect("test lock").remove(&slot);
            Ok(())
        }
    }

    fn frame(payload: &str) -> Vec<u8> {
        let mut data = Vec::new();
        data.extend_from_slice(&(payload.len() as u32).to_be_bytes());
        data.extend_from_slice(payload.as_bytes());
        data
    }

    fn read_test_frame(cursor: &mut Cursor<Vec<u8>>) -> String {
        read_frame(cursor).expect("test frame")
    }

    struct TestDuplex {
        read: Cursor<Vec<u8>>,
        written: Cursor<Vec<u8>>,
    }

    impl TestDuplex {
        fn new(read: Vec<u8>) -> Self {
            Self {
                read: Cursor::new(read),
                written: Cursor::new(Vec::new()),
            }
        }

        fn written_frames(mut self) -> Vec<String> {
            self.written.set_position(0);
            let mut frames = Vec::new();
            while (self.written.position() as usize) < self.written.get_ref().len() {
                frames.push(read_frame(&mut self.written).expect("written frame"));
            }
            frames
        }
    }

    impl Read for TestDuplex {
        fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
            self.read.read(buf)
        }
    }

    impl Write for TestDuplex {
        fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
            self.written.write(buf)
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    fn session_from_greeting(greeting: &str) -> String {
        greeting
            .split_whitespace()
            .find_map(|part| part.strip_prefix("session="))
            .expect("session")
            .to_string()
    }

    fn run_single_request(request: &str) -> Vec<String> {
        let store = SharedStore::with_sample_value();
        let broker = CredentialHandoffBroker::new(store);
        let mut stream = TestDuplex::new(frame(request));
        serve_backend_credential_transport_with_session(
            &mut stream,
            &mut { broker },
            11,
            LiveSessionAuthority::new_for_test(7),
        )
        .ok();
        stream.written_frames()
    }

    #[test]
    #[cfg(unix)]
    fn real_transport_pair_round_trip_uses_synthetic_secret() {
        let store = SharedStore::with_sample_value();
        let (parent, mut child) = UnixStream::pair().expect("socket pair");
        let handle = thread::spawn(move || {
            let broker = CredentialHandoffBroker::new(store);
            serve_backend_credential_transport(parent, broker, 11).ok();
        });
        let greeting = read_frame(&mut child).expect("greeting");
        let authority_hex = session_from_greeting(&greeting);
        let request = format!("{PROTOCOL} use canvas_default session={authority_hex}");
        write_frame(&mut child, &request).expect("request");
        let response = read_frame(&mut child).expect("response");
        assert!(response.contains("ok credential_hex="));
        assert!(response.contains(&encode_hex(SAMPLE_VALUE.as_bytes())));
        drop(child);
        handle.join().expect("server thread");
    }

    #[test]
    fn exact_authorized_session_succeeds_and_fake_session_fails() {
        let authority_hex = LiveSessionAuthority::new_for_test(7).as_hex();
        let ok = run_single_request(&format!(
            "{PROTOCOL} use canvas_default session={authority_hex}"
        ));
        assert!(ok[1].contains("ok credential_hex="));

        let fake = run_single_request(&format!("{PROTOCOL} use canvas_default session=bad"));
        assert!(fake[1].contains("unauthorized_or_stale_session"));
    }

    #[test]
    fn old_session_and_pid_reuse_do_not_authorize_after_restart() {
        let old_session = LiveSessionAuthority::new_for_test(1).as_hex();
        let mut stream = TestDuplex::new(frame(&format!(
            "{PROTOCOL} use canvas_default session={old_session}"
        )));
        let store = SharedStore::with_sample_value();
        let mut broker = CredentialHandoffBroker::new(store);
        serve_backend_credential_transport_with_session(
            &mut stream,
            &mut broker,
            11,
            LiveSessionAuthority::new_for_test(2),
        )
        .ok();
        let frames = stream.written_frames();
        let response = frames.last().expect("response");
        assert!(response.contains("unauthorized_or_stale_session"));
    }

    #[test]
    fn malformed_unsupported_version_operation_oversized_and_truncated_fail_closed() {
        assert!(run_single_request("")
            .last()
            .unwrap()
            .contains("malformed_request"));
        assert!(
            run_single_request("studyhub-credential/9 use canvas_default session=x")
                .last()
                .unwrap()
                .contains("unsupported_protocol")
        );
        let authority_hex = LiveSessionAuthority::new_for_test(7).as_hex();
        assert!(run_single_request(&format!(
            "{PROTOCOL} use unsupported session={authority_hex}"
        ))
        .last()
        .unwrap()
        .contains("unsupported_operation"));

        let mut oversized = Vec::new();
        oversized.extend_from_slice(&((MAX_FRAME_BYTES as u32) + 1).to_be_bytes());
        let mut cursor = Cursor::new(oversized);
        assert_eq!(
            read_frame(&mut cursor).unwrap_err().failure(),
            TransportFailure::OversizedFrame
        );

        let mut truncated = Cursor::new(vec![0, 0, 0, 10, b'a']);
        assert_eq!(
            read_frame(&mut truncated).unwrap_err().failure(),
            TransportFailure::TruncatedFrame
        );
    }

    #[test]
    fn debug_display_redact_secret_and_session_capability() {
        let authority = LiveSessionAuthority::new_for_test(7);
        assert!(!format!("{authority:?}").contains(&authority.as_hex()));
        let error = TransportError::new(TransportFailure::CredentialOperationFailed);
        assert!(!format!("{error:?}").contains(SAMPLE_VALUE));
        assert!(!error.to_string().contains(SAMPLE_VALUE));
    }

    #[test]
    fn closing_backend_channel_revokes_session_without_error() {
        let store = SharedStore::with_sample_value();
        let broker = CredentialHandoffBroker::new(store);
        let mut stream = Cursor::new(Vec::new());
        let mut broker = broker;
        assert!(serve_backend_credential_transport_with_session(
            &mut stream,
            &mut broker,
            11,
            LiveSessionAuthority::new_for_test(7),
        )
        .is_ok());
    }

    #[test]
    fn transport_errors_do_not_overwrite_existing_stored_credential() {
        let store = SharedStore::with_sample_value();
        store.fail_next(CredentialStoreFailure::OperationFailed);
        let mut broker = CredentialHandoffBroker::new(store.clone());
        let authority_hex = LiveSessionAuthority::new_for_test(7).as_hex();
        let mut stream = TestDuplex::new(frame(&format!(
            "{PROTOCOL} use canvas_default session={authority_hex}"
        )));
        serve_backend_credential_transport_with_session(
            &mut stream,
            &mut broker,
            11,
            LiveSessionAuthority::new_for_test(7),
        )
        .ok();
        assert_eq!(
            store
                .get_for_trusted_native_use(CredentialSlot::CanvasDefault)
                .expect("existing credential")
                .expose_for_trusted_native_use(),
            SAMPLE_VALUE
        );
    }
}
