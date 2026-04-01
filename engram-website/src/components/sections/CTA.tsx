"use client";

import { motion } from "motion/react";

export default function CTA() {
  return (
    <section className="relative z-10 py-32 px-6 min-h-[70vh] flex items-center justify-center">
      <div className="text-center max-w-xl mx-auto">
        <motion.h2
          initial={{ opacity: 0, y: 30, filter: "blur(6px)" }}
          whileInView={{ opacity: 1, y: 0, filter: "blur(0px)" }}
          viewport={{ once: true }}
          transition={{ type: "spring", stiffness: 40, damping: 18 }}
          className="text-4xl sm:text-5xl md:text-6xl font-semibold tracking-[-0.02em] leading-[1.1] mb-6"
        >
          Your <span className="text-[#E09F3E]">memory</span>.
          <br />
          Your data.
          <br />
          <span className="text-[#5D5A73]">Open source.</span>
        </motion.h2>

        <motion.p
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true }}
          transition={{ delay: 0.3, duration: 0.8 }}
          className="text-[#7E7B93] text-sm mb-10 max-w-sm mx-auto leading-relaxed"
        >
          MIT licensed. Your conversations never leave your infrastructure.
          No vendor lock-in.
        </motion.p>

        <motion.div
          initial={{ opacity: 0, y: 10 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ delay: 0.5, duration: 0.6 }}
          className="flex gap-3 justify-center"
        >
          <a
            href="https://github.com/abhishekdeore/engram"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 px-6 py-3 rounded-full bg-[#E09F3E] text-[#030014] text-sm font-medium cursor-pointer transition-all duration-200 hover:bg-[#F4B85C] hover:shadow-[0_0_40px_rgba(224,159,62,0.2)]"
          >
            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z" />
            </svg>
            Star on GitHub
          </a>
          <a
            href="/docs"
            className="inline-flex items-center px-6 py-3 rounded-full border border-[rgba(255,255,255,0.1)] text-sm font-medium text-[#7E7B93] cursor-pointer transition-all duration-200 hover:border-[rgba(255,255,255,0.2)] hover:text-[#E2DFD6] hover:bg-[rgba(255,255,255,0.04)]"
          >
            Read the docs
          </a>
        </motion.div>

        <motion.p
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true }}
          transition={{ delay: 0.8, duration: 1 }}
          className="mt-24 text-[10px] text-[#5D5A73] tracking-wider"
        >
          Built by{" "}
          <a
            href="https://github.com/abhishekdeore"
            target="_blank"
            rel="noopener noreferrer"
            className="text-[#7E7B93] hover:text-[#E09F3E] transition-colors"
          >
            Abhishek Deore
          </a>
          {" "}&middot; MIT License &middot; 2026
        </motion.p>
      </div>
    </section>
  );
}
