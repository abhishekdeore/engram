"use client";

import { useState, useEffect } from "react";

const REPO = "abhishekdeore/engram";
const POLL_INTERVAL = 60_000; // 60s

export default function GitHubStars() {
  const [stars, setStars] = useState<number | null>(null);

  useEffect(() => {
    let active = true;

    async function fetchStars() {
      try {
        const res = await fetch(`https://api.github.com/repos/${REPO}`, {
          headers: { Accept: "application/vnd.github.v3+json" },
        });
        if (!res.ok) return;
        const data = await res.json();
        if (active && typeof data.stargazers_count === "number") {
          setStars(data.stargazers_count);
        }
      } catch {
        // Silently fail -- badge just won't show a number
      }
    }

    fetchStars();
    const interval = setInterval(fetchStars, POLL_INTERVAL);

    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  return (
    <a
      href={`https://github.com/${REPO}`}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[11px] font-medium border border-[rgba(255,255,255,0.1)] bg-[#13121A]/80 backdrop-blur-md text-[#E2DFD6] cursor-pointer transition-all duration-200 hover:border-[rgba(255,255,255,0.2)] hover:bg-[rgba(255,255,255,0.08)] shadow-[0_2px_16px_rgba(0,0,0,0.5)]"
    >
      {/* GitHub star icon */}
      <svg className="w-3.5 h-3.5 text-[#E09F3E]" fill="currentColor" viewBox="0 0 16 16">
        <path d="M8 .25a.75.75 0 0 1 .673.418l1.882 3.815 4.21.612a.75.75 0 0 1 .416 1.279l-3.046 2.97.719 4.192a.75.75 0 0 1-1.088.791L8 12.347l-3.766 1.98a.75.75 0 0 1-1.088-.79l.72-4.194L.818 6.374a.75.75 0 0 1 .416-1.28l4.21-.611L7.327.668A.75.75 0 0 1 8 .25z" />
      </svg>
      {stars !== null ? (
        <span>{stars}</span>
      ) : (
        <span className="w-4 h-2 rounded bg-[rgba(255,255,255,0.1)] animate-pulse" />
      )}
    </a>
  );
}
