import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
} from "react";
import type { ReactNode } from "react";
import { createElement } from "react";

interface ThemeContextValue {
  isDark: boolean;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  isDark: false,
  toggle: () => {},
});

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [isDark, setIsDark] = useState(() => {
    // Honor the initial data-theme declared on <html> per entry point
    // (report/index.html defaults to dark; mini-app entries set their own).
    return document.documentElement.dataset.theme === "dark";
  });

  useEffect(() => {
    document.documentElement.dataset.theme = isDark ? "dark" : "light";
  }, [isDark]);

  const toggle = useCallback(() => {
    setIsDark((prev) => !prev);
  }, []);

  return createElement(ThemeContext.Provider, { value: { isDark, toggle } }, children);
}

export function useTheme() {
  return useContext(ThemeContext);
}
