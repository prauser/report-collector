import { clsx, type ClassValue } from "clsx";

export function cn(...inputs: ClassValue[]) {
  return clsx(inputs);
}

export function formatPrice(val: number | null): string {
  if (!val) return "-";
  return val.toLocaleString("ko-KR") + "원";
}

export function sentimentLabel(val: string | null): {
  label: string;
  color: string;
} {
  if (!val) return { label: "-", color: "text-gray-400" };
  const n = parseFloat(val);
  if (n >= 0.6) return { label: "매우 긍정", color: "text-green-600" };
  if (n >= 0.2) return { label: "긍정", color: "text-green-500" };
  if (n >= -0.2) return { label: "중립", color: "text-yellow-500" };
  if (n >= -0.6) return { label: "부정", color: "text-red-400" };
  return { label: "매우 부정", color: "text-red-600" };
}

export function opinionColor(op: string | null): string {
  if (!op) return "bg-gray-100 text-gray-600";
  if (["매수", "강력매수", "Buy", "Strong Buy"].includes(op))
    return "bg-green-100 text-green-700";
  if (["중립", "Hold", "보유"].includes(op))
    return "bg-yellow-100 text-yellow-700";
  if (["매도", "Sell"].includes(op)) return "bg-red-100 text-red-700";
  return "bg-blue-100 text-blue-700";
}
