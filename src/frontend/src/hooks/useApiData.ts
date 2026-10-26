import {useCallback, useEffect, useRef, useState} from "react";

interface ApiData<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

export function useApiData<T>(
  key: string,
  loader: () => Promise<T>,
): ApiData<T> {
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const [state, setState] = useState<ApiData<T>>({
    data: null,
    error: null,
    loading: true,
    reload: () => undefined,
  });
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((value) => value + 1), []);
  // The key whose data is on screen. Reloading it refetches in the
  // background and swaps the result in, so the page stays mounted (scroll
  // position, open menus, drafts) instead of flashing "Loading…"; only a
  // new key starts from the loading state.
  const shownKey = useRef<string | null>(null);

  useEffect(() => {
    let active = true;
    if (shownKey.current !== key) {
      shownKey.current = null;
      setState({data: null, error: null, loading: true, reload});
    }
    void loaderRef
      .current()
      .then((data) => {
        if (!active) return;
        shownKey.current = key;
        setState({data, error: null, loading: false, reload});
      })
      .catch((caught: unknown) => {
        if (active) {
          shownKey.current = null;
          setState({
            data: null,
            error: caught instanceof Error ? caught.message : "Request failed.",
            loading: false,
            reload,
          });
        }
      });
    return () => {
      active = false;
    };
  }, [key, revision, reload]);

  return state;
}

export function requireApiData<T>(
  data: T | undefined,
  response: Response,
  fallback: string,
): T {
  if (data === undefined) {
    throw new Error(response.status === 404 ? "Not found." : fallback);
  }
  return data;
}
