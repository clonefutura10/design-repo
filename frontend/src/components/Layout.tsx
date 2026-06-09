import { NavLink, Outlet } from "react-router-dom";
import { FileText, LayoutDashboard, ListOrdered } from "lucide-react";
import { clsx } from "clsx";

const nav = [
  { to: "/", icon: LayoutDashboard, label: "Upload & Annotate" },
  { to: "/jobs", icon: ListOrdered, label: "Job History" },
];

export default function Layout() {
  return (
    <div className="min-h-screen flex">
      {/* Sidebar */}
      <aside className="w-64 flex flex-col shrink-0" style={{ background: "#3C1053" }}>
        {/* Logo */}
        <div className="px-6 py-5 border-b border-white/10 flex items-center gap-3">
          <div className="rounded-btn p-1.5" style={{ background: "#6B2D88" }}>
            <FileText className="w-5 h-5 text-white" />
          </div>
          <div>
            <p className="font-semibold text-sm leading-tight text-white">aCRF Annotator</p>
            <p className="text-[11px] leading-tight" style={{ color: "rgba(255,255,255,0.5)" }}>
              AstraZeneca · SDTM Automation
            </p>
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 px-3 py-4 space-y-0.5">
          {nav.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                clsx(
                  "flex items-center gap-3 px-3 py-2.5 rounded-btn text-sm font-medium transition-colors",
                  isActive
                    ? "text-white"
                    : "hover:text-white"
                )
              }
              style={({ isActive }) =>
                isActive
                  ? { background: "#6B2D88" }
                  : { color: "rgba(255,255,255,0.65)" }
              }
            >
              <Icon className="w-4 h-4 shrink-0" />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="px-4 py-4 border-t border-white/10 text-[11px]" style={{ color: "rgba(255,255,255,0.3)" }}>
          AstraZeneca · SDTM v1.0
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 flex flex-col min-w-0">
        {/* Top bar */}
        <header className="bg-az-bg border-b border-az-border px-8 py-4 flex items-center justify-between shrink-0 shadow-az">
          <div className="flex items-center gap-3">
            <div className="w-1 h-6 rounded-full" style={{ background: "#6B2D88" }} />
            <span className="text-sm font-semibold" style={{ color: "#6B2D88" }}>
              aCRF Annotation Engine
            </span>
          </div>
          <p className="text-xs" style={{ color: "#6B6B6B" }}>
            Automated CRF → SDTM mapping · Powered by AstraZeneca standards
          </p>
        </header>

        <div className="flex-1 overflow-auto p-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
