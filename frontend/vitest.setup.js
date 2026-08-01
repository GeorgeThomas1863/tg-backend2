import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// RTL's auto-cleanup needs a global afterEach, which Vitest only provides
// with globals:true. Register cleanup explicitly so renders never leak
// between tests.
afterEach(cleanup);

// jsdom does not implement HTMLMediaElement playback and logs
// "Not implemented" errors if play()/pause()/load() are ever invoked
// (VideoPlayer renders <video autoPlay>). Stub them so mounting a
// player in tests is silent and deterministic.
window.HTMLMediaElement.prototype.play = () => Promise.resolve();
window.HTMLMediaElement.prototype.pause = () => {};
window.HTMLMediaElement.prototype.load = () => {};
