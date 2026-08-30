import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import {api, setCsrfToken} from "../api/client";
import type {components} from "../api/schema";

type SessionPayload = components["schemas"]["BrowserSessionResponse"];
type User = SessionPayload["user"];

interface SessionContextValue {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({children}: PropsWithChildren) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const applySession = useCallback((payload: SessionPayload | null) => {
    setUser(payload?.user ?? null);
    setCsrfToken(payload?.csrf_token ?? null);
  }, []);

  useEffect(() => {
    void api.GET("/api/_ui/session", {}).then(({data}) => {
      applySession(data ?? null);
      setLoading(false);
    });
  }, [applySession]);

  const login = useCallback(
    async (username: string, password: string) => {
      const {data, error, response} = await api.POST("/api/_ui/session", {
        body: {username, password},
      });
      if (error || !data) {
        throw new Error(
          response.status === 401
            ? "Invalid username or password."
            : "Sign in failed.",
        );
      }
      applySession(data);
    },
    [applySession],
  );

  const logout = useCallback(async () => {
    await api.DELETE("/api/_ui/session", {});
    applySession(null);
  }, [applySession]);

  const value = useMemo(
    () => ({user, loading, login, logout}),
    [user, loading, login, logout],
  );

  return (
    <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
  );
}

// The hook intentionally shares the provider's module and stable context type.
// eslint-disable-next-line react-refresh/only-export-components
export function useSession(): SessionContextValue {
  const context = useContext(SessionContext);
  if (!context) {
    throw new Error("useSession must be used within SessionProvider");
  }
  return context;
}
