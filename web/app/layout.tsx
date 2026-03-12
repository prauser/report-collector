import type { Metadata } from "next";
import "./globals.css";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Report Collector",
  description: "증권사 리포트 검색 및 분석",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko">
      <body className="bg-gray-50 min-h-screen antialiased">
        <nav className="bg-white border-b border-gray-200 sticky top-0 z-10">
          <div className="max-w-7xl mx-auto px-4 h-14 flex items-center gap-6">
            <Link href="/" className="font-bold text-gray-900 text-lg">
              📊 리포트 수집기
            </Link>
            <Link href="/" className="text-sm text-gray-600 hover:text-gray-900">
              리포트 검색
            </Link>
            <Link href="/stats" className="text-sm text-gray-600 hover:text-gray-900">
              통계
            </Link>
            <Link href="/backfill" className="text-sm text-gray-600 hover:text-gray-900">
              백필
            </Link>
            <Link href="/pending" className="text-sm text-gray-600 hover:text-gray-900">
              검토 대기
            </Link>
            <Link href="/settings" className="text-sm text-gray-600 hover:text-gray-900">
              설정
            </Link>
          </div>
        </nav>
        <main className="max-w-7xl mx-auto px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
