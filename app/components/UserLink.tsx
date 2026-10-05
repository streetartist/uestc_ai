import Link from "next/link";
export function UserLink({ id, name }: { id?: string | null; name: string }) {
  return id ? <Link className="user-link" href={`/people/${id}`}>{name}</Link> : <span>{name}</span>;
}
export function UserLinks({ users }: { users: { id: string; name: string }[] }) {
  return <>{users.map((user, index) => <span key={user.id}>{index > 0 && "、"}<UserLink id={user.id} name={user.name} /></span>)}</>;
}
