import {useEffect, useRef} from "react";

// Changes often arrive in bursts (a merge writes the pull request, an event
// and a comment); one reload covers them.
const SETTLE_MS = 250;

/**
 * Calls `onChange` whenever the issue or pull request `number` changes,
 * whoever changed it. After a dropped connection it also calls `onChange`
 * once on reconnecting, since changes in between were not announced.
 */
export function useLiveUpdates(
  owner: string,
  repo: string,
  number: number,
  onChange: () => void,
) {
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    if (typeof EventSource === "undefined" || !number) return;
    const source = new EventSource(
      `/api/_ui/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/issues/${number}/live`,
    );
    let timer: ReturnType<typeof setTimeout> | undefined;
    let dropped = false;
    const changed = () => {
      clearTimeout(timer);
      timer = setTimeout(() => onChangeRef.current(), SETTLE_MS);
    };
    source.onmessage = changed;
    source.onerror = () => {
      dropped = true;
    };
    source.onopen = () => {
      if (dropped) changed();
      dropped = false;
    };
    return () => {
      clearTimeout(timer);
      source.close();
    };
  }, [owner, repo, number]);
}
