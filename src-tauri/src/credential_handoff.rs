#![allow(dead_code)]

use std::fmt;

use crate::credential_store::{
    CredentialSlot, CredentialStore, CredentialStoreError, CredentialStoreFailure, SecretValue,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum HandoffRequest {
    UseCanvasDefault,
    UnsupportedSlot,
    Malformed,
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub(crate) struct BackendChildAuthorization {
    child_id: u32,
    generation: u64,
    marker: u64,
}

impl fmt::Debug for BackendChildAuthorization {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("BackendChildAuthorization")
            .field("child_id", &self.child_id)
            .field("generation", &self.generation)
            .field("marker", &"<redacted>")
            .finish()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum HandoffFailure {
    UnauthorizedChild,
    StaleAuthorization,
    SlotNotAllowed,
    MalformedRequest,
    MissingCredential,
    BackendUnavailable,
    AccessDenied,
    OperationFailed,
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub(crate) struct HandoffError {
    failure: HandoffFailure,
}

impl HandoffError {
    fn new(failure: HandoffFailure) -> Self {
        Self { failure }
    }

    pub(crate) fn failure(self) -> HandoffFailure {
        self.failure
    }
}

impl fmt::Debug for HandoffError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("HandoffError")
            .field("failure", &self.failure)
            .finish()
    }
}

impl fmt::Display for HandoffError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let message = match self.failure {
            HandoffFailure::UnauthorizedChild => "credential_handoff_unauthorized_child",
            HandoffFailure::StaleAuthorization => "credential_handoff_stale_authorization",
            HandoffFailure::SlotNotAllowed => "credential_handoff_slot_not_allowed",
            HandoffFailure::MalformedRequest => "credential_handoff_malformed_request",
            HandoffFailure::MissingCredential => "credential_handoff_missing_credential",
            HandoffFailure::BackendUnavailable => "credential_handoff_backend_unavailable",
            HandoffFailure::AccessDenied => "credential_handoff_access_denied",
            HandoffFailure::OperationFailed => "credential_handoff_operation_failed",
        };
        formatter.write_str(message)
    }
}

impl std::error::Error for HandoffError {}

pub(crate) struct CredentialHandoffBroker<S: CredentialStore> {
    store: S,
    authorized_child: Option<BackendChildAuthorization>,
    next_generation: u64,
}

impl<S: CredentialStore> CredentialHandoffBroker<S> {
    pub(crate) fn new(store: S) -> Self {
        Self {
            store,
            authorized_child: None,
            next_generation: 1,
        }
    }

    pub(crate) fn authorize_backend_child(&mut self, child_id: u32) -> BackendChildAuthorization {
        let generation = self.next_generation;
        self.next_generation = self.next_generation.saturating_add(1);
        let authorization = BackendChildAuthorization {
            child_id,
            generation,
            marker: generation.rotate_left(17) ^ 0x5d71_a550_4c3d_b291,
        };
        self.authorized_child = Some(authorization);
        authorization
    }

    pub(crate) fn restart_backend_child(&mut self, child_id: u32) -> BackendChildAuthorization {
        self.authorize_backend_child(child_id)
    }

    pub(crate) fn clear_backend_child(&mut self) {
        self.authorized_child = None;
    }

    pub(crate) fn with_credential_for_authorized_child<R>(
        &self,
        authorization: BackendChildAuthorization,
        request: HandoffRequest,
        action: impl FnOnce(&SecretValue) -> R,
    ) -> Result<R, HandoffError> {
        self.verify_authorization(authorization)?;
        let slot = slot_for_request(request)?;
        let secret = self
            .store
            .get_for_trusted_native_use(slot)
            .map_err(map_store_error)?;
        Ok(action(&secret))
    }

    fn verify_authorization(
        &self,
        authorization: BackendChildAuthorization,
    ) -> Result<(), HandoffError> {
        let Some(current) = self.authorized_child else {
            return Err(HandoffError::new(HandoffFailure::UnauthorizedChild));
        };
        if authorization.child_id == current.child_id
            && authorization.generation != current.generation
        {
            return Err(HandoffError::new(HandoffFailure::StaleAuthorization));
        }
        if authorization != current {
            return Err(HandoffError::new(HandoffFailure::UnauthorizedChild));
        }
        Ok(())
    }
}

fn slot_for_request(request: HandoffRequest) -> Result<CredentialSlot, HandoffError> {
    match request {
        HandoffRequest::UseCanvasDefault => Ok(CredentialSlot::CanvasDefault),
        HandoffRequest::UnsupportedSlot => Err(HandoffError::new(HandoffFailure::SlotNotAllowed)),
        HandoffRequest::Malformed => Err(HandoffError::new(HandoffFailure::MalformedRequest)),
    }
}

fn map_store_error(error: CredentialStoreError) -> HandoffError {
    let failure = match error.failure() {
        CredentialStoreFailure::Missing => HandoffFailure::MissingCredential,
        CredentialStoreFailure::BackendUnavailable => HandoffFailure::BackendUnavailable,
        CredentialStoreFailure::AccessDenied => HandoffFailure::AccessDenied,
        CredentialStoreFailure::OperationFailed => HandoffFailure::OperationFailed,
    };
    HandoffError::new(failure)
}

#[cfg(test)]
mod tests {
    use std::collections::{HashMap, HashSet};
    use std::sync::Mutex;

    use super::*;
    use crate::credential_store::CredentialAvailability;

    const SECRET: &str = "synthetic-canvas-credential";
    const LEAK_CANARY: &str = "synthetic-secret-bearing-error";

    #[derive(Default)]
    struct FakeCredentialStore {
        values: Mutex<HashMap<CredentialSlot, SecretValue>>,
        failures: Mutex<HashMap<CredentialSlot, CredentialStoreFailure>>,
        touched_slots: Mutex<HashSet<CredentialSlot>>,
    }

    impl FakeCredentialStore {
        fn with_canvas_secret() -> Self {
            let store = Self::default();
            store
                .store_replace(CredentialSlot::CanvasDefault, &SecretValue::new(SECRET))
                .expect("synthetic store");
            store
        }

        fn fail_get_for(&self, slot: CredentialSlot, failure: CredentialStoreFailure) {
            self.failures
                .lock()
                .expect("test lock")
                .insert(slot, failure);
        }

        fn touched(&self, slot: CredentialSlot) -> bool {
            self.touched_slots
                .lock()
                .expect("test lock")
                .contains(&slot)
        }
    }

    impl CredentialStore for FakeCredentialStore {
        fn store_replace(
            &self,
            slot: CredentialSlot,
            secret: &SecretValue,
        ) -> Result<(), CredentialStoreError> {
            self.values
                .lock()
                .expect("test lock")
                .insert(slot, secret.clone());
            Ok(())
        }

        fn exists(
            &self,
            slot: CredentialSlot,
        ) -> Result<CredentialAvailability, CredentialStoreError> {
            let availability = if self.values.lock().expect("test lock").contains_key(&slot) {
                CredentialAvailability::Configured
            } else {
                CredentialAvailability::Missing
            };
            Ok(availability)
        }

        fn get_for_trusted_native_use(
            &self,
            slot: CredentialSlot,
        ) -> Result<SecretValue, CredentialStoreError> {
            self.touched_slots.lock().expect("test lock").insert(slot);
            if let Some(failure) = self.failures.lock().expect("test lock").remove(&slot) {
                return Err(CredentialStoreError::new(failure));
            }
            self.values
                .lock()
                .expect("test lock")
                .get(&slot)
                .cloned()
                .ok_or_else(|| CredentialStoreError::new(CredentialStoreFailure::Missing))
        }

        fn delete(&self, slot: CredentialSlot) -> Result<(), CredentialStoreError> {
            self.values.lock().expect("test lock").remove(&slot);
            Ok(())
        }
    }

    #[test]
    fn authorized_child_can_use_narrow_canvas_credential_interface() {
        let store = FakeCredentialStore::with_canvas_secret();
        let mut broker = CredentialHandoffBroker::new(store);
        let child = broker.authorize_backend_child(42);

        let observed = broker
            .with_credential_for_authorized_child(
                child,
                HandoffRequest::UseCanvasDefault,
                |secret| secret.expose_for_trusted_native_use().to_string(),
            )
            .expect("authorized handoff");

        assert_eq!(observed, SECRET);
    }

    #[test]
    fn unknown_child_cannot_use_credential_interface() {
        let store = FakeCredentialStore::with_canvas_secret();
        let mut broker = CredentialHandoffBroker::new(store);
        let mut unknown = broker.authorize_backend_child(42);
        unknown.child_id = 99;

        let error = broker
            .with_credential_for_authorized_child(unknown, HandoffRequest::UseCanvasDefault, |_| {
                unreachable!("must not receive credential")
            })
            .unwrap_err();

        assert_eq!(error.failure(), HandoffFailure::UnauthorizedChild);
    }

    #[test]
    fn stale_child_authorization_is_rejected_after_restart() {
        let store = FakeCredentialStore::with_canvas_secret();
        let mut broker = CredentialHandoffBroker::new(store);
        let stale = broker.authorize_backend_child(42);
        let fresh = broker.restart_backend_child(42);

        assert_ne!(format!("{stale:?}"), format!("{fresh:?}"));
        let error = broker
            .with_credential_for_authorized_child(stale, HandoffRequest::UseCanvasDefault, |_| {
                unreachable!("must not receive credential")
            })
            .unwrap_err();
        assert_eq!(error.failure(), HandoffFailure::StaleAuthorization);
    }

    #[test]
    fn malformed_and_unsupported_requests_are_rejected_before_store_access() {
        let store = FakeCredentialStore::with_canvas_secret();
        let mut broker = CredentialHandoffBroker::new(store);
        let child = broker.authorize_backend_child(42);

        let malformed = broker
            .with_credential_for_authorized_child(child, HandoffRequest::Malformed, |_| {
                unreachable!("must not receive credential")
            })
            .unwrap_err();
        let unsupported = broker
            .with_credential_for_authorized_child(child, HandoffRequest::UnsupportedSlot, |_| {
                unreachable!("must not receive credential")
            })
            .unwrap_err();

        assert_eq!(malformed.failure(), HandoffFailure::MalformedRequest);
        assert_eq!(unsupported.failure(), HandoffFailure::SlotNotAllowed);
        assert!(!broker.store.touched(CredentialSlot::CanvasDefault));
    }

    #[test]
    fn missing_and_unavailable_credentials_fail_with_sanitized_categories() {
        let store = FakeCredentialStore::default();
        let mut broker = CredentialHandoffBroker::new(store);
        let child = broker.authorize_backend_child(42);

        let missing = broker
            .with_credential_for_authorized_child(child, HandoffRequest::UseCanvasDefault, |_| {
                unreachable!("must not receive credential")
            })
            .unwrap_err();
        assert_eq!(missing.failure(), HandoffFailure::MissingCredential);

        broker.store.fail_get_for(
            CredentialSlot::CanvasDefault,
            CredentialStoreFailure::BackendUnavailable,
        );
        let unavailable = broker
            .with_credential_for_authorized_child(child, HandoffRequest::UseCanvasDefault, |_| {
                unreachable!("must not receive credential")
            })
            .unwrap_err();
        assert_eq!(unavailable.failure(), HandoffFailure::BackendUnavailable);
    }

    #[test]
    fn diagnostics_do_not_expose_authorization_markers_or_secret_values() {
        let store = FakeCredentialStore::with_canvas_secret();
        let mut broker = CredentialHandoffBroker::new(store);
        let child = broker.authorize_backend_child(42);
        let auth_debug = format!("{child:?}");
        assert!(!auth_debug.contains(&child.marker.to_string()));

        let secret = broker
            .with_credential_for_authorized_child(
                child,
                HandoffRequest::UseCanvasDefault,
                |secret| secret.clone(),
            )
            .expect("authorized handoff");
        assert!(!format!("{secret:?}").contains(SECRET));

        let error = HandoffError::new(HandoffFailure::OperationFailed);
        assert!(!format!("{error:?}").contains(LEAK_CANARY));
        assert!(!error.to_string().contains(LEAK_CANARY));
    }
}
