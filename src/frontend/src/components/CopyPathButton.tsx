import {useState} from "react";

import {Octicon} from "./Octicon";

/** Copies a repository path to the clipboard. */
export function CopyPathButton({path}: {path: string}) {
  const [copied, setCopied] = useState(false);

  const copy = () => {
    void navigator.clipboard
      ?.writeText(path)
      .then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => undefined);
  };

  return (
    <button
      aria-label={copied ? "Path copied" : "Copy path"}
      className="icon-button"
      onClick={copy}
      title={copied ? "Copied!" : "Copy path"}
      type="button"
    >
      <Octicon name="copy" />
    </button>
  );
}
