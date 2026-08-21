// bun test preload: registers happy-dom globals (document, window, ...) so
// hook tests can render React components without a real browser. Loaded via
// `[test] preload` in bunfig.toml, before every test file.
import { GlobalRegistrator } from "@happy-dom/global-registrator";

GlobalRegistrator.register();
