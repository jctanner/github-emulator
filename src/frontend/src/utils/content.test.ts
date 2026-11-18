import {afterEach, describe, expect, it, vi} from "vitest";

import {
  decodeBase64Content,
  encodeBase64Bytes,
  withFileContent,
} from "./content";

afterEach(() => vi.unstubAllGlobals());

const file = {
  type: "file" as const,
  name: "big.txt",
  path: "docs/big.txt",
  sha: "abc",
  size: 1048576,
  url: "",
  git_url: "",
  html_url: "",
};

describe("withFileContent", () => {
  it("reads a file of 1 MiB or more in the raw media type", async () => {
    const requests: Request[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: Request) => {
        requests.push(input);
        return Promise.resolve(new Response("large file\n"));
      }),
    );

    const loaded = await withFileContent("octo", "demo", "feature/one", {
      ...file,
      encoding: "none",
      content: "",
    });

    expect(decodeBase64Content(loaded.content)).toBe("large file\n");
    expect(loaded.encoding).toBe("base64");
    expect(requests).toHaveLength(1);
    const url = new URL(requests[0].url);
    expect(decodeURIComponent(url.pathname)).toBe(
      "/api/v3/repos/octo/demo/contents/docs/big.txt",
    );
    expect(url.searchParams.get("ref")).toBe("feature/one");
    expect(requests[0].headers.get("Accept")).toBe(
      "application/vnd.github.raw",
    );
  });

  it("leaves a file that carries its content alone", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const small = {...file, encoding: "base64", content: "aGk="};

    expect(await withFileContent("octo", "demo", "main", small)).toBe(small);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("fails rather than show an empty file when the raw read fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("", {status: 500})),
    );

    await expect(
      withFileContent("octo", "demo", "main", {
        ...file,
        encoding: "none",
        content: "",
      }),
    ).rejects.toThrow("Could not load file.");
  });
});

describe("encodeBase64Bytes", () => {
  it("encodes more bytes than one call's argument limit", () => {
    const bytes = new Uint8Array(1024 * 1024 + 3).fill(65);
    expect(encodeBase64Bytes(bytes)).toBe(btoa("A".repeat(bytes.length)));
  });
});
