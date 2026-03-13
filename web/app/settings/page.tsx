"use client";
export const dynamic = "force-dynamic";

import { useEffect, useState } from "react";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface Channel {
  id: number;
  username: string;
  display_name: string | null;
  is_active: boolean;
}

async function fetchChannels(): Promise<Channel[]> {
  const res = await fetch(`${BASE}/api/channels`);
  if (!res.ok) throw new Error("fetch failed");
  return res.json();
}

export default function SettingsPage() {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [loading, setLoading] = useState(true);
  const [newUsername, setNewUsername] = useState("");
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = () => {
    setLoading(true);
    fetchChannels()
      .then(setChannels)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => { reload(); }, []);

  const handleToggle = async (id: number) => {
    await fetch(`${BASE}/api/channels/${id}/toggle`, { method: "PATCH" });
    reload();
  };

  const handleDelete = async (id: number, username: string) => {
    if (!confirm(`${username} 채널을 삭제할까요?`)) return;
    await fetch(`${BASE}/api/channels/${id}`, { method: "DELETE" });
    reload();
  };

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newUsername.trim()) return;
    setAdding(true);
    try {
      const res = await fetch(`${BASE}/api/channels`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: newUsername.trim() }),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      setNewUsername("");
      reload();
    } catch (e) {
      alert(`추가 실패: ${e}`);
    } finally {
      setAdding(false);
    }
  };

  return (
    <div className="max-w-2xl mx-auto px-4 py-8 space-y-6">
      <h1 className="text-2xl font-bold">설정 — 수집 채널</h1>

      {error && <p className="text-red-500 text-sm">{error}</p>}

      <div className="bg-white border rounded-lg divide-y">
        {loading ? (
          <p className="p-4 text-gray-400 text-sm">불러오는 중...</p>
        ) : channels.length === 0 ? (
          <p className="p-4 text-gray-400 text-sm">등록된 채널 없음</p>
        ) : (
          channels.map((ch) => (
            <div key={ch.id} className="flex items-center justify-between px-4 py-3">
              <div className="flex items-center gap-3">
                <button
                  onClick={() => handleToggle(ch.id)}
                  className={`w-10 h-5 rounded-full transition-colors ${
                    ch.is_active ? "bg-blue-500" : "bg-gray-300"
                  } relative`}
                  title={ch.is_active ? "비활성화" : "활성화"}
                >
                  <span
                    className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${
                      ch.is_active ? "translate-x-5" : "translate-x-0.5"
                    }`}
                  />
                </button>
                <div>
                  <span className={`font-mono text-sm ${ch.is_active ? "" : "text-gray-400 line-through"}`}>
                    {ch.username}
                  </span>
                  {ch.display_name && (
                    <span className="ml-2 text-xs text-gray-400">{ch.display_name}</span>
                  )}
                </div>
              </div>
              <button
                onClick={() => handleDelete(ch.id, ch.username)}
                className="text-xs text-red-400 hover:text-red-600 px-2 py-1 rounded hover:bg-red-50"
              >
                삭제
              </button>
            </div>
          ))
        )}
      </div>

      <form onSubmit={handleAdd} className="flex gap-2">
        <input
          type="text"
          value={newUsername}
          onChange={(e) => setNewUsername(e.target.value)}
          placeholder="@채널명 또는 t.me/채널명"
          className="flex-1 border rounded px-3 py-2 text-sm font-mono"
        />
        <button
          type="submit"
          disabled={adding || !newUsername.trim()}
          className="px-4 py-2 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 disabled:opacity-50"
        >
          {adding ? "추가 중..." : "추가"}
        </button>
      </form>

      <p className="text-xs text-gray-400">
        비활성화된 채널은 백필/리스너에서 제외됩니다. 변경사항은 즉시 반영됩니다 (백필 기준).
        리스너는 재시작 후 반영됩니다.
      </p>
    </div>
  );
}
