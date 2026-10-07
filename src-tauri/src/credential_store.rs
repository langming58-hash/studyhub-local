#![allow(dead_code)]

#[cfg(test)]
use std::collections::{HashMap, HashSet};
use std::fmt;
#[cfg(test)]
use std::sync::Mutex;

use crate::RuntimeProfile;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash)]
pub(crate) enum CredentialSlot {
    CanvasDefault,
    DevelopmentSmokeTest,
}

impl CredentialSlot {
    fn account_name(self) -> &'static str {
        match self {
            Self::CanvasDefault => "canvas.default",
            Self::DevelopmentSmokeTest => "development.smoke-test",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum CredentialAvailability {
    Configured,
    Missing,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum CredentialStoreFailure {
    Missing,
    BackendUnavailable,
    AccessDenied,
    OperationFailed,
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub(crate) struct CredentialStoreError {
    failure: CredentialStoreFailure,
}

impl CredentialStoreError {
    pub(crate) fn new(failure: CredentialStoreFailure) -> Self {
        Self { failure }
    }

    pub(crate) fn failure(self) -> CredentialStoreFailure {
        self.failure
    }
}

impl fmt::Debug for CredentialStoreError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CredentialStoreError")
            .field("failure", &self.failure)
            .finish()
    }
}

impl fmt::Display for CredentialStoreError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let message = match self.failure {
            CredentialStoreFailure::Missing => "credential_missing",
            CredentialStoreFailure::BackendUnavailable => "credential_backend_unavailable",
            CredentialStoreFailure::AccessDenied => "credential_access_denied",
            CredentialStoreFailure::OperationFailed => "credential_operation_failed",
        };
        formatter.write_str(message)
    }
}

impl std::error::Error for CredentialStoreError {}

pub(crate) type CredentialStoreResult<T> = Result<T, CredentialStoreError>;

#[derive(Clone, Eq, PartialEq)]
pub(crate) struct SecretValue(String);

impl SecretValue {
    pub(crate) fn new(value: impl Into<String>) -> Self {
        Self(value.into())
    }

    pub(crate) fn expose_for_trusted_native_use(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for SecretValue {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("SecretValue(<redacted>)")
    }
}

pub(crate) trait CredentialStore {
    fn store_replace(
        &self,
        slot: CredentialSlot,
        secret: &SecretValue,
    ) -> CredentialStoreResult<()>;
    fn exists(&self, slot: CredentialSlot) -> CredentialStoreResult<CredentialAvailability>;
    fn get_for_trusted_native_use(
        &self,
        slot: CredentialSlot,
    ) -> CredentialStoreResult<SecretValue>;
    fn delete(&self, slot: CredentialSlot) -> CredentialStoreResult<()>;
}

pub(crate) fn namespace_for_profile(profile: RuntimeProfile) -> &'static str {
    match profile {
        RuntimeProfile::Production => "io.studyhublocal.desktop.credentials",
        RuntimeProfile::Development => "io.studyhublocal.desktop.dev.credentials",
        RuntimeProfile::DemoTest => "io.studyhublocal.desktop.demo.credentials",
    }
}

pub(crate) enum CredentialStoreBackend {
    Native(NativeCredentialStore),
    DemoDenied(DemoDeniedCredentialStore),
}

impl CredentialStore for CredentialStoreBackend {
    fn store_replace(
        &self,
        slot: CredentialSlot,
        secret: &SecretValue,
    ) -> CredentialStoreResult<()> {
        match self {
            Self::Native(store) => store.store_replace(slot, secret),
            Self::DemoDenied(store) => store.store_replace(slot, secret),
        }
    }

    fn exists(&self, slot: CredentialSlot) -> CredentialStoreResult<CredentialAvailability> {
        match self {
            Self::Native(store) => store.exists(slot),
            Self::DemoDenied(store) => store.exists(slot),
        }
    }

    fn get_for_trusted_native_use(
        &self,
        slot: CredentialSlot,
    ) -> CredentialStoreResult<SecretValue> {
        match self {
            Self::Native(store) => store.get_for_trusted_native_use(slot),
            Self::DemoDenied(store) => store.get_for_trusted_native_use(slot),
        }
    }

    fn delete(&self, slot: CredentialSlot) -> CredentialStoreResult<()> {
        match self {
            Self::Native(store) => store.delete(slot),
            Self::DemoDenied(store) => store.delete(slot),
        }
    }
}

pub(crate) fn credential_store_for_profile(profile: RuntimeProfile) -> CredentialStoreBackend {
    match profile {
        RuntimeProfile::Production | RuntimeProfile::Development => CredentialStoreBackend::Native(
            NativeCredentialStore::new(namespace_for_profile(profile)),
        ),
        RuntimeProfile::DemoTest => CredentialStoreBackend::DemoDenied(DemoDeniedCredentialStore),
    }
}

pub(crate) struct NativeCredentialStore {
    namespace: &'static str,
}

impl NativeCredentialStore {
    fn new(namespace: &'static str) -> Self {
        Self { namespace }
    }

    fn entry(&self, slot: CredentialSlot) -> CredentialStoreResult<keyring::Entry> {
        keyring::Entry::new(self.namespace, slot.account_name()).map_err(map_keyring_error)
    }
}

impl CredentialStore for NativeCredentialStore {
    fn store_replace(
        &self,
        slot: CredentialSlot,
        secret: &SecretValue,
    ) -> CredentialStoreResult<()> {
        self.entry(slot)?
            .set_password(secret.expose_for_trusted_native_use())
            .map_err(map_keyring_error)
    }

    fn exists(&self, slot: CredentialSlot) -> CredentialStoreResult<CredentialAvailability> {
        match self.entry(slot)?.get_password() {
            Ok(_) => Ok(CredentialAvailability::Configured),
            Err(keyring::Error::NoEntry) => Ok(CredentialAvailability::Missing),
            Err(error) => Err(map_keyring_error(error)),
        }
    }

    fn get_for_trusted_native_use(
        &self,
        slot: CredentialSlot,
    ) -> CredentialStoreResult<SecretValue> {
        self.entry(slot)?
            .get_password()
            .map(SecretValue::new)
            .map_err(map_keyring_error)
    }

    fn delete(&self, slot: CredentialSlot) -> CredentialStoreResult<()> {
        self.entry(slot)?
            .delete_credential()
            .map_err(map_keyring_error)
    }
}

fn map_keyring_error(error: keyring::Error) -> CredentialStoreError {
    let failure = match error {
        keyring::Error::NoEntry => CredentialStoreFailure::Missing,
        keyring::Error::NoDefaultStore => CredentialStoreFailure::BackendUnavailable,
        keyring::Error::NoStorageAccess(_) => CredentialStoreFailure::AccessDenied,
        keyring::Error::PlatformFailure(_) => CredentialStoreFailure::OperationFailed,
        _ => CredentialStoreFailure::OperationFailed,
    };
    CredentialStoreError::new(failure)
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct DemoDeniedCredentialStore;

impl CredentialStore for DemoDeniedCredentialStore {
    fn store_replace(
        &self,
        _slot: CredentialSlot,
        _secret: &SecretValue,
    ) -> CredentialStoreResult<()> {
        Err(CredentialStoreError::new(
            CredentialStoreFailure::BackendUnavailable,
        ))
    }

    fn exists(&self, _slot: CredentialSlot) -> CredentialStoreResult<CredentialAvailability> {
        Err(CredentialStoreError::new(
            CredentialStoreFailure::BackendUnavailable,
        ))
    }

    fn get_for_trusted_native_use(
        &self,
        _slot: CredentialSlot,
    ) -> CredentialStoreResult<SecretValue> {
        Err(CredentialStoreError::new(
            CredentialStoreFailure::BackendUnavailable,
        ))
    }

    fn delete(&self, _slot: CredentialSlot) -> CredentialStoreResult<()> {
        Err(CredentialStoreError::new(
            CredentialStoreFailure::BackendUnavailable,
        ))
    }
}

#[cfg(test)]
#[derive(Default)]
pub(crate) struct MemoryCredentialStore {
    namespace: &'static str,
    values: Mutex<HashMap<(&'static str, CredentialSlot), SecretValue>>,
    failing_slots: Mutex<HashSet<CredentialSlot>>,
}

#[cfg(test)]
impl MemoryCredentialStore {
    pub(crate) fn new(namespace: &'static str) -> Self {
        Self {
            namespace,
            values: Mutex::new(HashMap::new()),
            failing_slots: Mutex::new(HashSet::new()),
        }
    }

    pub(crate) fn fail_next_store_for(&self, slot: CredentialSlot) {
        self.failing_slots.lock().expect("test lock").insert(slot);
    }
}

#[cfg(test)]
impl CredentialStore for MemoryCredentialStore {
    fn store_replace(
        &self,
        slot: CredentialSlot,
        secret: &SecretValue,
    ) -> CredentialStoreResult<()> {
        if self.failing_slots.lock().expect("test lock").remove(&slot) {
            return Err(CredentialStoreError::new(
                CredentialStoreFailure::OperationFailed,
            ));
        }
        self.values
            .lock()
            .expect("test lock")
            .insert((self.namespace, slot), secret.clone());
        Ok(())
    }

    fn exists(&self, slot: CredentialSlot) -> CredentialStoreResult<CredentialAvailability> {
        let availability = if self
            .values
            .lock()
            .expect("test lock")
            .contains_key(&(self.namespace, slot))
        {
            CredentialAvailability::Configured
        } else {
            CredentialAvailability::Missing
        };
        Ok(availability)
    }

    fn get_for_trusted_native_use(
        &self,
        slot: CredentialSlot,
    ) -> CredentialStoreResult<SecretValue> {
        self.values
            .lock()
            .expect("test lock")
            .get(&(self.namespace, slot))
            .cloned()
            .ok_or_else(|| CredentialStoreError::new(CredentialStoreFailure::Missing))
    }

    fn delete(&self, slot: CredentialSlot) -> CredentialStoreResult<()> {
        if self
            .values
            .lock()
            .expect("test lock")
            .remove(&(self.namespace, slot))
            .is_some()
        {
            Ok(())
        } else {
            Err(CredentialStoreError::new(CredentialStoreFailure::Missing))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SLOT: CredentialSlot = CredentialSlot::CanvasDefault;
    const SYNTHETIC_SECRET: &str = "synthetic-credential-value";
    const REPLACEMENT_SECRET: &str = "synthetic-replacement-value";
    const SYNTHETIC_ERROR: &str = "synthetic-secret-bearing-platform-error";

    #[derive(Debug)]
    struct SyntheticPlatformError(&'static str);

    impl fmt::Display for SyntheticPlatformError {
        fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter.write_str(self.0)
        }
    }

    impl std::error::Error for SyntheticPlatformError {}

    fn platform_error(message: &'static str) -> Box<dyn std::error::Error + Send + Sync> {
        Box::new(SyntheticPlatformError(message))
    }

    fn assert_sanitized(error: CredentialStoreError, expected: CredentialStoreFailure) {
        assert_eq!(error.failure(), expected);
        assert!(!format!("{error:?}").contains(SYNTHETIC_ERROR));
        assert!(!error.to_string().contains(SYNTHETIC_ERROR));
    }

    #[test]
    fn memory_store_covers_store_exists_get_replace_delete() {
        let store = MemoryCredentialStore::new(namespace_for_profile(RuntimeProfile::Development));
        assert_eq!(store.exists(SLOT), Ok(CredentialAvailability::Missing));

        store
            .store_replace(SLOT, &SecretValue::new(SYNTHETIC_SECRET))
            .expect("synthetic store");
        assert_eq!(store.exists(SLOT), Ok(CredentialAvailability::Configured));
        assert_eq!(
            store
                .get_for_trusted_native_use(SLOT)
                .expect("trusted test retrieval")
                .expose_for_trusted_native_use(),
            SYNTHETIC_SECRET
        );

        store
            .store_replace(SLOT, &SecretValue::new(REPLACEMENT_SECRET))
            .expect("synthetic replace");
        assert_eq!(
            store
                .get_for_trusted_native_use(SLOT)
                .expect("trusted test retrieval")
                .expose_for_trusted_native_use(),
            REPLACEMENT_SECRET
        );

        store.delete(SLOT).expect("synthetic delete");
        assert_eq!(store.exists(SLOT), Ok(CredentialAvailability::Missing));
        assert_eq!(
            store
                .get_for_trusted_native_use(SLOT)
                .unwrap_err()
                .failure(),
            CredentialStoreFailure::Missing
        );
    }

    #[test]
    fn failed_store_does_not_overwrite_existing_value() {
        let store = MemoryCredentialStore::new(namespace_for_profile(RuntimeProfile::Development));
        store
            .store_replace(SLOT, &SecretValue::new(SYNTHETIC_SECRET))
            .expect("synthetic store");
        store.fail_next_store_for(SLOT);

        let error = store
            .store_replace(SLOT, &SecretValue::new(REPLACEMENT_SECRET))
            .unwrap_err();
        assert_eq!(error.failure(), CredentialStoreFailure::OperationFailed);
        assert_eq!(
            store
                .get_for_trusted_native_use(SLOT)
                .expect("trusted test retrieval")
                .expose_for_trusted_native_use(),
            SYNTHETIC_SECRET
        );
    }

    #[test]
    fn keyring_error_mapping_is_conservative_and_sanitized() {
        assert_sanitized(
            map_keyring_error(keyring::Error::NoEntry),
            CredentialStoreFailure::Missing,
        );
        assert_sanitized(
            map_keyring_error(keyring::Error::NoDefaultStore),
            CredentialStoreFailure::BackendUnavailable,
        );
        assert_sanitized(
            map_keyring_error(keyring::Error::NoStorageAccess(platform_error(
                SYNTHETIC_ERROR,
            ))),
            CredentialStoreFailure::AccessDenied,
        );
        assert_sanitized(
            map_keyring_error(keyring::Error::PlatformFailure(platform_error(
                SYNTHETIC_ERROR,
            ))),
            CredentialStoreFailure::OperationFailed,
        );
        assert_sanitized(
            map_keyring_error(keyring::Error::Invalid(
                "synthetic-parameter".to_string(),
                SYNTHETIC_ERROR.to_string(),
            )),
            CredentialStoreFailure::OperationFailed,
        );
    }

    #[test]
    fn error_classification_does_not_modify_stored_credentials() {
        let store = MemoryCredentialStore::new(namespace_for_profile(RuntimeProfile::Development));
        store
            .store_replace(SLOT, &SecretValue::new(SYNTHETIC_SECRET))
            .expect("synthetic store");

        let classified = map_keyring_error(keyring::Error::PlatformFailure(platform_error(
            SYNTHETIC_ERROR,
        )));
        assert_eq!(
            classified.failure(),
            CredentialStoreFailure::OperationFailed
        );
        assert_eq!(
            store
                .get_for_trusted_native_use(SLOT)
                .expect("trusted test retrieval")
                .expose_for_trusted_native_use(),
            SYNTHETIC_SECRET
        );
    }

    #[test]
    fn profile_namespaces_are_isolated() {
        assert_ne!(
            namespace_for_profile(RuntimeProfile::Production),
            namespace_for_profile(RuntimeProfile::Development)
        );
        assert_ne!(
            namespace_for_profile(RuntimeProfile::Production),
            namespace_for_profile(RuntimeProfile::DemoTest)
        );
    }

    #[test]
    fn demo_test_uses_denied_backend_not_native_storage() {
        assert!(matches!(
            credential_store_for_profile(RuntimeProfile::DemoTest),
            CredentialStoreBackend::DemoDenied(_)
        ));
        assert!(matches!(
            credential_store_for_profile(RuntimeProfile::Production),
            CredentialStoreBackend::Native(_)
        ));
        let denied = credential_store_for_profile(RuntimeProfile::DemoTest);
        assert_eq!(
            denied.exists(SLOT).unwrap_err().failure(),
            CredentialStoreFailure::BackendUnavailable
        );
    }

    #[test]
    fn secret_values_and_errors_are_redacted() {
        let secret = SecretValue::new(SYNTHETIC_SECRET);
        assert!(!format!("{secret:?}").contains(SYNTHETIC_SECRET));

        let error = CredentialStoreError::new(CredentialStoreFailure::OperationFailed);
        assert!(!format!("{error:?}").contains(SYNTHETIC_SECRET));
        assert!(!error.to_string().contains(SYNTHETIC_SECRET));
    }

    #[test]
    #[ignore = "development-only opt-in smoke test; touches the Development Keychain namespace"]
    fn development_keychain_smoke_test_opt_in() {
        if std::env::var("STUDYHUB_RUN_DEV_KEYCHAIN_SMOKE").as_deref() != Ok("1") {
            return;
        }

        let store = NativeCredentialStore::new(namespace_for_profile(RuntimeProfile::Development));
        let slot = CredentialSlot::DevelopmentSmokeTest;
        let first = SecretValue::new(format!(
            "synthetic-development-keychain-smoke-{}-first",
            std::process::id()
        ));
        let second = SecretValue::new(format!(
            "synthetic-development-keychain-smoke-{}-second",
            std::process::id()
        ));

        let _ = store.delete(slot);
        store
            .store_replace(slot, &first)
            .expect("store synthetic credential");
        assert_eq!(store.exists(slot), Ok(CredentialAvailability::Configured));
        assert_eq!(
            store
                .get_for_trusted_native_use(slot)
                .expect("retrieve synthetic credential")
                .expose_for_trusted_native_use(),
            first.expose_for_trusted_native_use()
        );

        store
            .store_replace(slot, &second)
            .expect("replace synthetic credential");
        assert_eq!(
            store
                .get_for_trusted_native_use(slot)
                .expect("retrieve replacement credential")
                .expose_for_trusted_native_use(),
            second.expose_for_trusted_native_use()
        );

        store.delete(slot).expect("delete synthetic credential");
        assert_eq!(store.exists(slot), Ok(CredentialAvailability::Missing));
    }
}
