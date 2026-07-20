// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TaskSwitch } from "../TaskSwitch";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

describe("TaskSwitch", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  it("returns to the last subview used inside a task", async () => {
    const onChange = vi.fn();
    await act(async () => {
      root.render(<TaskSwitch mode="timeline" onChange={onChange} />);
    });
    const byText = (label: string) =>
      [...container.querySelectorAll("button")].find(
        (button) => button.textContent === label,
      );
    await act(async () => byText("Transaction Detail")?.click());
    expect(onChange).toHaveBeenLastCalledWith("transactions");

    await act(async () => {
      root.render(<TaskSwitch mode="transactions" onChange={onChange} />);
    });
    await act(async () => byText("Money Trail")?.click());
    expect(onChange).toHaveBeenLastCalledWith("timeline");
  });
});
