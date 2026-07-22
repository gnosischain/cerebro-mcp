// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FilterDrawer } from "../FilterDrawer";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

function installMatchMedia(matches: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({
      matches,
      media: "(min-width: 900px)",
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  Reflect.deleteProperty(window, "matchMedia");
});

describe("FilterDrawer", () => {
  it("is structural toolbar content on desktop without a details element", async () => {
    installMatchMedia(true);
    await act(async () => root.render(
      <FilterDrawer><button type="button">Token filter</button></FilterDrawer>,
    ));

    expect(container.querySelector("details")).toBeNull();
    expect(container.querySelector(".ge-topbar-filters")?.hasAttribute("hidden")).toBe(false);
    expect(container.querySelector(".ge-filter-drawer__toggle")?.getAttribute("aria-expanded"))
      .toBe("true");
  });

  it("can keep secondary controls collapsed on desktop", async () => {
    installMatchMedia(true);
    await act(async () => root.render(
      <FilterDrawer collapsibleOnDesktop>
        <button type="button">Token filter</button>
      </FilterDrawer>,
    ));
    const toggle = container.querySelector<HTMLButtonElement>(
      ".ge-filter-drawer__toggle",
    );
    const panel = container.querySelector<HTMLElement>(".ge-topbar-filters");

    expect(panel?.hidden).toBe(true);
    expect(toggle?.getAttribute("aria-expanded")).toBe("false");

    await act(async () => toggle?.click());
    expect(panel?.hidden).toBe(false);
    expect(toggle?.getAttribute("aria-expanded")).toBe("true");

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
      await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
    });
    expect(panel?.hidden).toBe(true);
    expect(document.activeElement).toBe(toggle);
  });

  it("opens on compact viewports and Escape restores focus to its toggle", async () => {
    installMatchMedia(false);
    await act(async () => root.render(
      <FilterDrawer><button type="button">Token filter</button></FilterDrawer>,
    ));
    const toggle = container.querySelector<HTMLButtonElement>(
      ".ge-filter-drawer__toggle",
    );
    const panel = container.querySelector<HTMLElement>(".ge-topbar-filters");
    expect(panel?.hidden).toBe(true);

    await act(async () => toggle?.click());
    expect(panel?.hidden).toBe(false);
    expect(toggle?.getAttribute("aria-expanded")).toBe("true");

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
      await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
    });
    expect(panel?.hidden).toBe(true);
    expect(document.activeElement).toBe(toggle);
  });
});
