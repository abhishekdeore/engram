"use client";

import { motion } from "motion/react";

const TITLE_WORDS = ["Memory", "across", "every", "LLM", "you", "use."];

const PROVIDERS = [
  { name: "ChatGPT", color: "#10A37F" },
  { name: "Claude", color: "#D97706" },
  { name: "Gemini", color: "#4285F4" },
  { name: "Grok", color: "#E2DFD6" },
  { name: "Copilot", color: "#6366F1" },
];

export default function Hero() {
  return (
    <section className="relative z-10 flex flex-col items-center justify-center min-h-screen px-6 text-center">
      {/* Engram wordmark */}
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.8, delay: 0.2 }}
        className="mb-8 flex flex-col items-center"
      >
        <span className="relative text-base sm:text-lg font-semibold tracking-[0.25em] uppercase text-[#E09F3E]">
          Engram
          <span
            className="absolute inset-0 blur-xl opacity-30 pointer-events-none"
            style={{ background: "radial-gradient(circle, #E09F3E 0%, transparent 70%)" }}
            aria-hidden="true"
          />
        </span>
        <span className="mt-3 px-3.5 py-1 rounded-full text-[10px] font-mono tracking-[0.2em] uppercase text-[#FFF3E0] border border-[rgba(224,159,62,0.25)] bg-[rgba(224,159,62,0.12)]">
          Persistent memory for AI
        </span>
      </motion.div>

      {/* Main title */}
      <h1 className="text-5xl sm:text-6xl md:text-7xl lg:text-[5.5rem] font-semibold leading-[1.05] tracking-[-0.02em] max-w-4xl">
        {TITLE_WORDS.map((word, i) => (
          <motion.span
            key={word + i}
            initial={{ opacity: 0, y: 50, filter: "blur(8px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            transition={{
              type: "spring",
              stiffness: 50,
              damping: 20,
              delay: 0.4 + i * 0.1,
            }}
            className={`inline-block mr-[0.25em] ${
              word === "Memory" ? "text-[#E09F3E]" : ""
            }`}
          >
            {word}
          </motion.span>
        ))}
      </h1>

      {/* Subtitle */}
      <motion.p
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.8, delay: 1.2 }}
        className="mt-8 text-base sm:text-lg text-[#7E7B93] max-w-lg leading-relaxed"
      >
        Store your conversations verbatim. Ask any AI to recall what you
        discussed with another. Word for word. Zero hallucination.
      </motion.p>

      {/* Provider pills */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.8, delay: 1.6 }}
        className="flex flex-wrap gap-2.5 mt-10 justify-center"
      >
        {PROVIDERS.map((p, i) => (
          <motion.span
            key={p.name}
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 1.7 + i * 0.08 }}
            className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full text-xs font-medium cursor-default transition-colors duration-200"
            style={{
              border: `1px solid ${p.color}25`,
              color: `${p.color}CC`,
              backgroundColor: `${p.color}0A`,
            }}
          >
            <span
              className="w-1.5 h-1.5 rounded-full"
              style={{ backgroundColor: p.color }}
            />
            {p.name}
          </motion.span>
        ))}
      </motion.div>

      {/* CTAs */}
      <motion.div
        initial={{ opacity: 0, y: 15 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, delay: 2.0 }}
        className="flex gap-3 mt-12"
      >
        <a
          href="https://github.com/abhishekdeore/engram"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 px-5 py-2.5 rounded-full text-sm font-medium bg-[#E09F3E] text-[#030014] cursor-pointer transition-all duration-200 hover:bg-[#F4B85C] hover:shadow-[0_0_30px_rgba(224,159,62,0.25)]"
        >
          <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
            <path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z" />
          </svg>
          View on GitHub
        </a>
        <a
          href="#how-it-works"
          className="inline-flex items-center gap-2 px-5 py-2.5 rounded-full text-sm font-medium border border-[rgba(255,255,255,0.1)] text-[#7E7B93] cursor-pointer transition-all duration-200 hover:border-[rgba(255,255,255,0.2)] hover:text-[#E2DFD6] hover:bg-[rgba(255,255,255,0.04)]"
        >
          How it works
        </a>
      </motion.div>

      {/* Scroll indicator */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 0.3 }}
        transition={{ delay: 3, duration: 1.5 }}
        className="absolute bottom-10 left-1/2 -translate-x-1/2"
      >
        <motion.div
          animate={{ y: [0, 6, 0] }}
          transition={{ repeat: Infinity, duration: 2.5, ease: "easeInOut" }}
          className="w-[1px] h-10 bg-gradient-to-b from-transparent via-[#E09F3E] to-transparent opacity-40"
        />
      </motion.div>
    </section>
  );
}
