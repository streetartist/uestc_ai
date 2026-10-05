import Link from "next/link";
import { ArrowRight, FolderOpen } from "lucide-react";
import type { ReactNode } from "react";

export function CommunityEmpty({ title, description, href, action }: { title: string; description: string; href?: string; action?: string }) {
  return <div className="community-empty"><span className="community-empty-icon"><FolderOpen size={22} /></span><div><h3>{title}</h3><p>{description}</p>{href && action && <Link className="text-button" href={href}>{action}<ArrowRight size={15} /></Link>}</div></div>;
}

export function CommunityCard({ href, title, category, description, context, author, date }: { href: string; title: string; category: string; description?: string; context?: string; author?: ReactNode; date?: string }) {
  return <article className="community-card"><Link className="community-card-main" href={href}><div className="community-card-top"><span className="community-category">{category}</span>{context && <span className="community-card-context">{context}</span>}</div><h2>{title}<ArrowRight size={19} /></h2>{description && <p>{description}</p>}</Link>{(author || date) && <div className="community-card-footer">{author && <span>{author}</span>}{date && <time>{date}</time>}</div>}</article>;
}
