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

  useEffect(() => {
    let active = true;
    setState({data: null, error: null, loading: true, reload});
    void loaderRef
      .current()
      .then((data) => {
        if (active) setState({data, error: null, loading: false, reload});
      })
      .catch((caught: unknown) => {
        if (active) {
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
