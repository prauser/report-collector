"use client";

import { useState, useRef, DragEvent, ChangeEvent } from "react";
import Link from "next/link";
import { Upload, FileText, Check, AlertCircle } from "lucide-react";
import { api, TradeBase, TradeUploadResponse } from "@/lib/api";

const BROKERS = [
  { value: "", label: "자동 감지" },
  { value: "mirae", label: "미래에셋" },
  { value: "samsung", label: "삼성증권" },
  { value: "kiwoom", label: "키움증권" },
];

function formatAmount(val: number | null | undefined): string {
  if (val === null || val === undefined) return "-";
  return val.toLocaleString("ko-KR") + "원";
}

function formatDate(dateStr: string): string {
  return dateStr.slice(0, 10);
}

function SideBadge({ side }: { side: string }) {
  if (side === "buy") {
    return (
      <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">
        매수
      </span>
    );
  }
  return (
    <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700">
      매도
    </span>
  );
}

function PreviewTable({ rows }: { rows: TradeBase[] }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
        <span className="text-sm font-medium text-gray-700">
          파싱 결과 미리보기 — {rows.length}건
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="text-left px-4 py-2 font-medium text-gray-600 w-28">날짜</th>
              <th className="text-left px-4 py-2 font-medium text-gray-600">종목</th>
              <th className="text-left px-4 py-2 font-medium text-gray-600 w-20">구분</th>
              <th className="text-right px-4 py-2 font-medium text-gray-600 w-20">수량</th>
              <th className="text-right px-4 py-2 font-medium text-gray-600 w-32">금액</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map((row, i) => (
              <tr key={i} className="hover:bg-gray-50">
                <td className="px-4 py-2 text-gray-500 whitespace-nowrap">
                  {formatDate(row.traded_at)}
                </td>
                <td className="px-4 py-2">
                  <div className="font-medium text-gray-900">{row.name ?? row.symbol}</div>
                  {row.name && <div className="text-xs text-gray-400">{row.symbol}</div>}
                </td>
                <td className="px-4 py-2">
                  <SideBadge side={row.side} />
                </td>
                <td className="px-4 py-2 text-right text-gray-700">
                  {row.quantity.toLocaleString("ko-KR")}
                </td>
                <td className="px-4 py-2 text-right font-medium text-gray-900">
                  {formatAmount(row.amount)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

type UploadPhase = "idle" | "previewing" | "preview_ready" | "uploading" | "done" | "error";

export default function CsvUploader() {
  const [phase, setPhase] = useState<UploadPhase>("idle");
  const [isDragging, setIsDragging] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [broker, setBroker] = useState("");
  const [preview, setPreview] = useState<TradeBase[] | null>(null);
  const [result, setResult] = useState<TradeUploadResponse | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);

  async function runPreview(file: File) {
    setPhase("previewing");
    setErrorMsg(null);
    try {
      const res = await api.trades.upload(file, broker || undefined, true);
      setPreview(res.preview);
      setPhase("preview_ready");
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
      setPhase("error");
    }
  }

  function handleFileSelect(file: File) {
    if (phase === "previewing" || phase === "uploading") return;
    if (!file.name.endsWith(".csv")) {
      setErrorMsg("CSV 파일만 업로드할 수 있습니다.");
      setPhase("error");
      return;
    }
    setSelectedFile(file);
    setPreview(null);
    setResult(null);
    setErrorMsg(null);
    runPreview(file);
  }

  function handleInputChange(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) handleFileSelect(file);
  }

  function handleDragOver(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(true);
  }

  function handleDragLeave(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(false);
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFileSelect(file);
  }

  async function handleUpload() {
    if (!selectedFile) return;
    setPhase("uploading");
    setErrorMsg(null);
    try {
      const res = await api.trades.upload(selectedFile, broker || undefined, false);
      setResult(res);
      setPhase("done");
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
      setPhase("error");
    }
  }

  function handleReset() {
    setPhase("idle");
    setSelectedFile(null);
    setPreview(null);
    setResult(null);
    setErrorMsg(null);
    if (inputRef.current) inputRef.current.value = "";
  }

  const isBusy = phase === "previewing" || phase === "uploading";

  const dropZoneClass = [
    "relative flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed p-12 transition-colors cursor-pointer",
    isDragging
      ? "border-blue-400 bg-blue-50"
      : "border-gray-300 bg-gray-50 hover:border-gray-400 hover:bg-gray-100",
    isBusy ? "pointer-events-none opacity-50" : "",
  ].join(" ");

  return (
    <div className="space-y-6">
      {/* broker selector */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <label className="block text-sm font-medium text-gray-700 mb-2">
          브로커 (선택)
        </label>
        <select
          value={broker}
          onChange={(e) => setBroker(e.target.value)}
          disabled={phase === "uploading" || phase === "previewing"}
          className="w-full sm:w-48 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
          aria-label="브로커 선택"
        >
          {BROKERS.map((b) => (
            <option key={b.value} value={b.value}>
              {b.label}
            </option>
          ))}
        </select>
        <p className="mt-1 text-xs text-gray-400">비워두면 CSV 헤더에서 자동 감지합니다.</p>
      </div>

      {/* drop zone */}
      <div
        className={dropZoneClass}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        role="button"
        aria-label="CSV 파일 업로드 영역"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".csv"
          className="sr-only"
          onChange={handleInputChange}
          disabled={isBusy}
          aria-label="CSV 파일 선택"
        />
        {phase === "idle" || phase === "error" ? (
          <>
            <Upload className="w-10 h-10 text-gray-400" />
            <div className="text-center">
              <p className="text-sm font-medium text-gray-700">
                CSV 파일을 드래그하거나 클릭하세요
              </p>
              <p className="text-xs text-gray-400 mt-1">.csv 파일만 지원</p>
            </div>
          </>
        ) : (
          <>
            <FileText className="w-10 h-10 text-blue-400" />
            <div className="text-center">
              <p className="text-sm font-medium text-gray-700">{selectedFile?.name}</p>
              <p className="text-xs text-gray-400 mt-1">
                {phase === "previewing" ? "파싱 중..." : `${preview?.length ?? 0}건 파싱됨`}
              </p>
            </div>
          </>
        )}
      </div>

      {/* loading spinner for preview */}
      {phase === "previewing" && (
        <div className="flex items-center gap-2 text-sm text-gray-500" aria-live="polite">
          <div className="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
          미리보기 로딩 중...
        </div>
      )}

      {/* error message */}
      {phase === "error" && errorMsg && (
        <div className="flex items-start gap-3 bg-red-50 border border-red-200 rounded-xl p-4" role="alert">
          <AlertCircle className="w-5 h-5 text-red-500 shrink-0 mt-0.5" />
          <div className="text-sm text-red-700 whitespace-pre-wrap">{errorMsg}</div>
        </div>
      )}

      {/* preview table */}
      {phase === "preview_ready" && preview !== null && (
        preview.length === 0 ? (
          <div className="flex items-start gap-3 bg-yellow-50 border border-yellow-200 rounded-xl p-4" role="status">
            <AlertCircle className="w-5 h-5 text-yellow-500 shrink-0 mt-0.5" />
            <div className="text-sm text-yellow-800">
              파싱된 거래 내역이 없습니다. CSV 형식을 확인해주세요.
            </div>
          </div>
        ) : (
          <>
            <PreviewTable rows={preview} />
            <div className="flex items-center gap-3">
              <button
                onClick={handleUpload}
                className="px-5 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors"
              >
                저장 ({preview.length}건)
              </button>
              <button
                onClick={handleReset}
                className="px-4 py-2 text-sm text-gray-500 hover:text-gray-700"
              >
                취소
              </button>
            </div>
          </>
        )
      )}

      {/* uploading */}
      {phase === "uploading" && (
        <div className="flex items-center gap-2 text-sm text-gray-500" aria-live="polite">
          <div className="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
          저장 중...
        </div>
      )}

      {/* done (success only) */}
      {phase === "done" && result && (
        <div className="space-y-4">
          <div className="flex items-start gap-3 bg-green-50 border border-green-200 rounded-xl p-4" role="status">
            <Check className="w-5 h-5 text-green-600 shrink-0 mt-0.5" />
            <div className="text-sm text-green-800">
              <p className="font-medium">업로드 완료</p>
              <p className="mt-1">
                {result.inserted}건 저장, {result.skipped}건 중복 스킵
              </p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <Link
              href="/trades"
              className="text-sm text-blue-600 hover:text-blue-800 hover:underline"
            >
              체결 목록 보기 →
            </Link>
            <button
              onClick={handleReset}
              className="text-sm text-gray-500 hover:text-gray-700"
            >
              다시 업로드
            </button>
          </div>
        </div>
      )}

      {/* error phase — retry button */}
      {phase === "error" && (
        <div className="flex items-center gap-4">
          <button
            onClick={handleReset}
            className="px-4 py-2 text-sm text-gray-500 hover:text-gray-700"
          >
            다시 시도
          </button>
        </div>
      )}
    </div>
  );
}
