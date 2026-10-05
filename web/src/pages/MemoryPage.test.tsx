// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MemoryPage from "./MemoryPage";

const mocks = vi.hoisted(() => ({ load: vi.fn(), save: vi.fn() }));
vi.mock("@/lib/memory-api", () => ({ memoryApi: {
  getMemoryDocument: mocks.load, saveMemoryDocument: mocks.save,
} }));
vi.mock("@/contexts/useProfileScope", () => ({
  useProfileScope: () => ({ profile: "steam_player" }),
}));

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function button(label: string) {
  const result = [...container.querySelectorAll("button")].find((item) => item.textContent === label);
  if (!result) throw new Error(`Missing button: ${label}`);
  return result;
}

beforeEach(async () => {
  vi.clearAllMocks();
  mocks.load.mockResolvedValue({ content: "Saved note", version: "version-one" });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root.render(<MemoryPage />));
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("personal memory editor", () => {
  it("keeps the loaded document and reports a concurrent-edit conflict", async () => {
    mocks.save.mockRejectedValue(new Error("Memory changed; reload before saving"));
    await act(async () => button("Save").click());
    expect(mocks.save).toHaveBeenCalledWith("memory", "Saved note", "version-one");
    expect(container.querySelector("textarea")?.value).toBe("Saved note");
    expect(container.querySelector('[role="status"]')?.textContent).toContain("reload before saving");
    expect(button("Save").disabled).toBe(false);
  });

  it("pins the target while saving and loads the chosen document afterwards", async () => {
    let finish: (value: { content: string; version: string }) => void = () => undefined;
    mocks.save.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await act(async () => button("Save").click());
    expect(button("About you").disabled).toBe(true);
    await act(async () => finish({ content: "Saved note", version: "version-two" }));
    mocks.load.mockResolvedValue({ content: "Player preferences", version: "user-version" });
    await act(async () => button("About you").click());
    expect(mocks.load).toHaveBeenLastCalledWith("user");
    expect(container.querySelector("textarea")?.value).toBe("Player preferences");
    expect(button("About you").getAttribute("aria-pressed")).toBe("true");
  });
});
