export const dynamic = "force-dynamic";

import { Suspense } from "react";
import { api } from "@/lib/api";
import ReportFilters from "@/components/ReportFilters";
import ReportTable from "@/components/ReportTable";
import Pagination from "@/components/Pagination";

interface SearchParams {
  q?: string;
  stock?: string;
  broker?: string;
  opinion?: string;
  report_type?: string;
  channel?: string;
  from_date?: string;
  to_date?: string;
  has_ai?: string;
  page?: string;
}

export default async function HomePage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp = await searchParams;
  const page = parseInt(sp.page ?? "1");

  const [filtersResult, reportsResult] = await Promise.allSettled([
    api.reports.filters(),
    api.reports.list({
      q: sp.q,
      stock: sp.stock,
      broker: sp.broker,
      opinion: sp.opinion,
      report_type: sp.report_type,
      channel: sp.channel,
      from_date: sp.from_date,
      to_date: sp.to_date,
      has_ai: sp.has_ai === "true" ? true : sp.has_ai === "false" ? false : undefined,
      page,
      limit: 30,
    }),
  ]);

  const filters =
    filtersResult.status === "fulfilled"
      ? filtersResult.value
      : { brokers: [], opinions: [], report_types: [], channels: [] };

  const reports =
    reportsResult.status === "fulfilled"
      ? reportsResult.value
      : { total: 0, page: 1, limit: 30, items: [] };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900">리포트 검색</h1>
        <span className="text-sm text-gray-500">
          총 {reports.total.toLocaleString()}건
        </span>
      </div>

      <Suspense>
        <ReportFilters filters={filters} />
      </Suspense>

      <ReportTable reports={reports.items} />

      <Suspense>
        <Pagination total={reports.total} page={reports.page} limit={reports.limit} />
      </Suspense>
    </div>
  );
}
