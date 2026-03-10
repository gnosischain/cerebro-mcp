import { useState, useCallback, useEffect } from "react";

export function useTheme() {
  const [isDark, setIsDark] = useState(() => {
    document.documentElement.dataset.theme = "light";
    return false;
  });

  useEffect(() => {
    document.documentElement.dataset.theme = isDark ? "dark" : "light";
  }, [isDark]);

  const toggle = useCallback(() => {
    setIsDark((prev) => !prev);
  }, []);

  return { isDark, toggle };
}
