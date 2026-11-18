"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const items = [
  { href: "/studies", label: "Studies", icon: "⌁" },
  { href: "/personas", label: "Personas", icon: "◌" },
  { href: "/settings", label: "Settings", icon: "⚙" },
];

function isActive(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function PrimaryNavigation({ mobile = false }: { mobile?: boolean }) {
  const pathname = usePathname();
  const className = mobile ? "mobile-nav" : "sidebar-nav";

  return (
    <nav className={className} aria-label="Main navigation">
      {items.map((item) => {
        const active = isActive(pathname, item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={active ? "is-active" : undefined}
          >
            {!mobile ? <span aria-hidden="true">{item.icon}</span> : null}
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
