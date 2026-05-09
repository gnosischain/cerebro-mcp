import { Sun, Moon } from "lucide-react";
import { useTheme } from "../../hooks/useTheme";

export function MaThemeToggle() {
  const { isDark, toggle } = useTheme();
  return (
    <button
      className="ma-theme-toggle"
      onClick={toggle}
      title="Toggle theme"
      aria-label="Toggle theme"
    >
      {isDark ? <Sun size={14} /> : <Moon size={14} />}
    </button>
  );
}
