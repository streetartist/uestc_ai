"use client";
import { useEffect } from "react";
import Link from "next/link";
import { AppShell } from "@/app/components/AppShell";
import { useSession } from "@/app/components/SessionProvider";
import { PageLoading } from "@/app/components/ui";
export default function AccountPage() {
  const { user, loading } = useSession();
  useEffect(() => { if (user) window.location.replace(`/people/${user.id}?tab=settings`); }, [user]);
  return <AppShell title="个人主页" eyebrow="ACCOUNT">{loading || user ? <PageLoading /> : <Link className="primary-button" href="/login?next=/account">登录后进入个人主页</Link>}</AppShell>;
}
