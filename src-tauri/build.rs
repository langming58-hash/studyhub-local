fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "choose_study_folder",
            "choose_study_files",
            "restart_backend",
            "retry_backend",
            "startup_diagnostics",
        ]),
    ))
    .expect("failed to build StudyHub Local desktop permissions")
}
