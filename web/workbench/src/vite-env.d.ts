interface ImportMetaEnv {
  readonly VITE_ROADGEN_API_BASE?: string;
  readonly VITE_ROADGEN_VIEWER_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
