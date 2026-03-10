import { useState, useEffect } from "react";
import {
  ChevronLeft,
  ChevronRight,
  BarChart3,
  TrendingUp,
  PieChart,
  FileText,
  Activity,
  Layers,
  Database,
  Zap,
} from "lucide-react";
import type { HtmlSection } from "../types";

// Rotate through icons for sections
const SECTION_ICONS = [
  BarChart3,
  TrendingUp,
  PieChart,
  Activity,
  Layers,
  Database,
  Zap,
  FileText,
];

interface Props {
  sections: HtmlSection[];
  activeIndex: number;
  onChange: (index: number) => void;
}

export function Sidebar({ sections, activeIndex, onChange }: Props) {
  const [collapsed, setCollapsed] = useState(false);

  // Auto-collapse on small screens
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 768px)");
    if (mq.matches) setCollapsed(true);
    const handler = (e: MediaQueryListEvent) => setCollapsed(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  if (sections.length < 2) return null;

  return (
    <aside className={`sidebar no-print${collapsed ? " collapsed" : ""}`}>
      <div className="sidebar-header">
        {!collapsed && (
          <span className="sidebar-title">Sections</span>
        )}
        <button
          className="sidebar-toggle"
          onClick={() => setCollapsed((prev) => !prev)}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
      </div>

      <nav className="sidebar-nav">
        {sections.map((section, i) => {
          const Icon = SECTION_ICONS[i % SECTION_ICONS.length];
          const isActive = i === activeIndex;
          return (
            <button
              key={i}
              className={`sidebar-item${isActive ? " active" : ""}`}
              onClick={() => onChange(i)}
              title={section.title}
            >
              <Icon size={18} className="sidebar-icon" />
              {!collapsed && (
                <span className="sidebar-label">{section.title}</span>
              )}
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
