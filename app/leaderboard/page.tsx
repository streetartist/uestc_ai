"use client";

import Link from "next/link";
import { UserLinks } from "@/app/components/UserLink";
import { useCallback, useEffect, useState } from "react";
import { ArrowRight, Medal, RefreshCw } from "lucide-react";
import { AppShell } from "@/app/components/AppShell";
import { EmptyState, PageError, PageLoading } from "@/app/components/ui";
import { api, formatApiError } from "@/app/lib/api";
import type { Competition, LeaderboardRow } from "@/app/lib/domain";

export default function LeaderboardPage() {
  const [competitions, setCompetitions] = useState<Competition[]>([]);
  const [competition, setCompetition] = useState<Competition | null>(null);
  const [track, setTrack] = useState("");
  const [rows, setRows] = useState<LeaderboardRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadCompetitions = useCallback(async () => {
    try {
      const list = await api<Competition[]>("/competitions?status=published");
      setCompetitions(list);
      if (list[0]) setCompetition(await api<Competition>(`/competitions/${list[0].slug}`));
    } catch (requestError) { setError(formatApiError(requestError)); }
  }, []);

  useEffect(() => {
    let active = true;
    Promise.resolve().then(() => { if (active) return loadCompetitions(); });
    return () => { active = false; };
  }, [loadCompetitions]);
  useEffect(() => {
    if (!competition) return;
    let active = true;
    Promise.resolve().then(() => {
      if (!active) return;
      setLoading(true); setError("");
      return api<LeaderboardRow[]>(`/leaderboards/${competition.slug}${track ? `?track=${encodeURIComponent(track)}` : ""}`)
        .then((result) => { if (active) setRows(result); })
        .catch((requestError) => { if (active) setError(formatApiError(requestError)); })
        .finally(() => { if (active) setLoading(false); });
    });
    return () => { active = false; };
  }, [competition, track]);

  async function changeCompetition(id: string) {
    const summary = competitions.find((item) => item.id === id);
    if (!summary) return;
    setTrack(""); setCompetition(await api<Competition>(`/competitions/${summary.slug}`));
  }

  return (
    <AppShell title="榜单" eyebrow="LEADERBOARD" actions={<button className="outline-button" onClick={() => setTrack((value) => value)}><RefreshCw size={14} />刷新</button>}>
      <div className="leaderboard-controls"><label><span>赛事</span><select value={competition?.id ?? ""} onChange={(event) => void changeCompetition(event.target.value)}>{competitions.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label><label><span>赛道</span><select value={track} onChange={(event) => setTrack(event.target.value)}><option value="">全部赛道</option>{competition?.tracks?.map((item) => <option value={item.slug} key={item.id}>{item.name}</option>)}</select></label><p>比赛结束后公布最终榜单。外部评分与在线评审按赛事配置合并，所有来源完成后统一计算排名。</p></div>
      {loading ? <PageLoading label="正在读取榜单" /> : error ? <PageError message={error} /> : !rows.length ? <EmptyState title="榜单尚未公布" description="比赛结束且外部评分、在线评审均完成后，最终排名会在这里公布。" /> : <div className="leaderboard-table" role="table"><div className="leaderboard-head" role="row"><span>排名</span><span>队伍 / 作品</span><span>题目</span><span>成绩</span><span>来源</span><span /></div>{rows.map((row, index) => <div className="leaderboard-row" role="row" key={`${row.submission_id}-${row.batch}-${index}`}><strong className="rank-cell">{row.rank ?? index + 1}{(row.rank ?? index + 1) <= 3 && <Medal size={14} />}</strong><div><strong>{row.team}</strong>{row.team_members?.length ? <small><UserLinks users={row.team_members} /></small> : null}<span>{row.work} · v{row.version}</span></div><span>{row.problem}<small>{row.track}</small></span><strong>{row.total_score?.toFixed(2) ?? "—"}</strong><span>{row.batch}<small>{row.source === "online-review" ? "在线评审" : row.source}</small></span><Link href={`/works/${row.submission_id}`} aria-label={`查看 ${row.work}`}><ArrowRight size={16} /></Link></div>)}</div>}
    </AppShell>
  );
}
