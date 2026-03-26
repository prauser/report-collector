"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useEffect } from "react";
import {
  FileText,
  BarChart2,
  TrendingUp,
  Bot,
  MoreHorizontal,
  RefreshCw,
  Clock,
  Settings,
  X,
  LineChart,
} from "lucide-react";

// Desktop nav groups
const desktopGroups = [
  {
    label: "리포트",
    items: [
      { href: "/", label: "리포트 검색" },
      { href: "/stats", label: "통계" },
    ],
  },
  {
    label: "매매",
    items: [{ href: "/trades", label: "매매 저널" }],
  },
  {
    label: "분석",
    items: [{ href: "/analysis", label: "종목분석" }],
  },
  {
    label: "AI Agent",
    items: [{ href: "/agent", label: "Agent" }],
  },
  {
    label: "관리",
    items: [
      { href: "/backfill", label: "백필" },
      { href: "/pending", label: "검토 대기" },
      { href: "/settings", label: "설정" },
    ],
  },
];

// Mobile bottom tabs (main 3)
const mobileTabs = [
  { href: "/", label: "리포트", icon: FileText },
  { href: "/trades", label: "매매", icon: TrendingUp },
  { href: "/agent", label: "Agent", icon: Bot },
];

// Mobile "more" menu items
const moreItems = [
  { href: "/stats", label: "통계", icon: BarChart2 },
  { href: "/analysis", label: "종목분석", icon: LineChart },
  { href: "/backfill", label: "백필", icon: RefreshCw },
  { href: "/pending", label: "검토 대기", icon: Clock },
  { href: "/settings", label: "설정", icon: Settings },
];

function isActive(href: string, pathname: string | null): boolean {
  if (!pathname) return false;
  if (href === "/") return pathname === "/";
  return pathname.startsWith(href);
}

export default function Navigation() {
  const pathname = usePathname();
  const [moreOpen, setMoreOpen] = useState(false);

  // Close the overlay whenever the route changes
  useEffect(() => {
    setMoreOpen(false);
  }, [pathname]);

  const isMoreActive = moreItems.some((item) => isActive(item.href, pathname));

  return (
    <>
      {/* Desktop nav — hidden on mobile */}
      <nav aria-label="메인 메뉴" className="hidden md:flex bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 h-14 flex items-center gap-4 w-full">
          <Link href="/" className="font-bold text-gray-900 text-lg shrink-0 mr-2">
            리포트 수집기
          </Link>

          {desktopGroups.map((group, gi) => (
            <div key={group.label} className="flex items-center gap-0.5">
              {gi > 0 && (
                <span className="mr-2 text-gray-200 select-none">|</span>
              )}
              <span className="text-xs text-gray-400 font-medium uppercase tracking-wide px-1 select-none">
                {group.label}
              </span>
              {group.items.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`px-3 py-1.5 rounded-md text-sm transition-colors ${
                    isActive(item.href, pathname)
                      ? "bg-gray-100 text-gray-900 font-medium"
                      : "text-gray-600 hover:text-gray-900 hover:bg-gray-50"
                  }`}
                >
                  {item.label}
                </Link>
              ))}
            </div>
          ))}
        </div>
      </nav>

      {/* Mobile top header — visible only on mobile */}
      <header className="md:hidden bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="px-4 h-12 flex items-center">
          <Link href="/" className="font-bold text-gray-900 text-base">
            리포트 수집기
          </Link>
        </div>
      </header>

      {/* Mobile bottom tab bar */}
      <nav aria-label="하단 탭 메뉴" className="md:hidden fixed bottom-0 left-0 right-0 z-20 bg-white border-t border-gray-200">
        <div className="flex items-stretch h-16">
          {mobileTabs.map(({ href, label, icon: Icon }) => {
            const active = isActive(href, pathname);
            return (
              <Link
                key={href}
                href={href}
                className={`flex-1 flex flex-col items-center justify-center gap-0.5 text-xs transition-colors ${
                  active ? "text-blue-600" : "text-gray-500 hover:text-gray-900"
                }`}
              >
                <Icon size={20} strokeWidth={active ? 2.5 : 1.75} />
                <span>{label}</span>
              </Link>
            );
          })}

          {/* More button */}
          <button
            onClick={() => setMoreOpen((v) => !v)}
            aria-expanded={moreOpen}
            aria-label="더보기 메뉴"
            className={`flex-1 flex flex-col items-center justify-center gap-0.5 text-xs transition-colors ${
              isMoreActive || moreOpen
                ? "text-blue-600"
                : "text-gray-500 hover:text-gray-900"
            }`}
          >
            <MoreHorizontal
              size={20}
              strokeWidth={isMoreActive || moreOpen ? 2.5 : 1.75}
            />
            <span>더보기</span>
          </button>
        </div>
      </nav>

      {/* Mobile "more" overlay */}
      {moreOpen && (
        <div
          className="md:hidden fixed inset-0 z-30"
          onClick={() => setMoreOpen(false)}
          onKeyDown={(e) => { if (e.key === "Escape") setMoreOpen(false); }}
          role="presentation"
        >
          {/* Backdrop */}
          <div className="absolute inset-0 bg-black/30" />

          {/* Menu panel */}
          <div
            className="absolute bottom-16 left-0 right-0 bg-white border-t border-gray-200 shadow-lg"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
              <span className="text-sm font-medium text-gray-700">더보기</span>
              <button
                onClick={() => setMoreOpen(false)}
                aria-label="닫기"
                className="p-1 text-gray-400 hover:text-gray-600"
              >
                <X size={18} />
              </button>
            </div>
            <div className="grid grid-cols-2">
              {moreItems.map(({ href, label, icon: Icon }) => {
                const active = isActive(href, pathname);
                return (
                  <Link
                    key={href}
                    href={href}
                    onClick={() => setMoreOpen(false)}
                    className={`flex items-center gap-3 px-5 py-4 text-sm border-b border-gray-50 transition-colors ${
                      active
                        ? "text-blue-600 bg-blue-50 font-medium"
                        : "text-gray-700 hover:bg-gray-50"
                    }`}
                  >
                    <Icon size={18} strokeWidth={active ? 2.5 : 1.75} />
                    {label}
                  </Link>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* Mobile bottom spacer so content isn't hidden behind the tab bar */}
      <div className="md:hidden h-16" aria-hidden="true" />
    </>
  );
}
