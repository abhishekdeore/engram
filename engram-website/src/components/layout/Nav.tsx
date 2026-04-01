"use client";

import { motion } from "motion/react";
import GitHubStars from "@/components/ui/GitHubStars";

const NAV_LINKS = [
  { label: "About", href: "#about" },
  { label: "Resources", href: "#resources" },
  { label: "Docs", href: "/docs" },
];

export default function Nav() {
  return (
    <motion.nav
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, delay: 0.1 }}
      className="fixed top-6 right-6 sm:right-10 z-50 flex items-center gap-2.5"
    >
      {/* Nav links pill */}
      <div className="hidden sm:flex items-center gap-1 bg-[#13121A]/80 backdrop-blur-md border border-[rgba(255,255,255,0.08)] rounded-full px-1.5 py-1 shadow-[0_2px_16px_rgba(0,0,0,0.5)]">
        {NAV_LINKS.map((link) => (
          <a
            key={link.label}
            href={link.href}
            {...(link.href.startsWith("http")
              ? { target: "_blank", rel: "noopener noreferrer" }
              : {})}
            className="px-3.5 py-1.5 text-xs font-medium text-[#7E7B93] rounded-full transition-colors hover:text-[#E2DFD6] hover:bg-[rgba(255,255,255,0.06)]"
          >
            {link.label}
          </a>
        ))}
      </div>

      {/* Stars */}
      <GitHubStars />

      {/* Launching soon */}
      <span className="hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[10px] font-medium tracking-wider uppercase border border-[#E09F3E22] bg-[#13121A]/80 backdrop-blur-md text-[#E09F3E] shadow-[0_2px_16px_rgba(0,0,0,0.5)]">
        <span className="relative flex h-1.5 w-1.5">
          <span className="absolute inline-flex h-full w-full rounded-full bg-[#E09F3E] opacity-60 animate-ping" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-[#E09F3E]" />
        </span>
        Launching soon
      </span>
    </motion.nav>
  );
}
