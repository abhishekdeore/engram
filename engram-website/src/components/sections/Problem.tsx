"use client";

import { motion } from "motion/react";

const SILOS = [
  {
    provider: "ChatGPT",
    color: "#10A37F",
    snippet: '"Research quantum computing error correction for me..."',
  },
  {
    provider: "Claude",
    color: "#D97706",
    snippet: '"I have no context about your previous research."',
  },
  {
    provider: "Gemini",
    color: "#4285F4",
    snippet: '"I don\'t have access to conversations from other AI tools."',
  },
];

export default function Problem() {
  return (
    <section id="about" className="relative z-10 py-32 px-6">
      <div className="max-w-5xl mx-auto">
        <motion.p
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-100px" }}
          transition={{ duration: 0.6 }}
          className="text-xs font-mono tracking-[0.25em] uppercase text-[#5D5A73] mb-4"
        >
          The problem
        </motion.p>

        <motion.h2
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-100px" }}
          transition={{ duration: 0.6, delay: 0.1 }}
          className="text-3xl sm:text-4xl md:text-5xl font-semibold tracking-[-0.02em] max-w-2xl leading-[1.1] mb-16"
        >
          Every AI starts from zero.
          <span className="text-[#5D5A73]"> Every single time.</span>
        </motion.h2>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
          {SILOS.map((silo, i) => (
            <motion.div
              key={silo.provider}
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-50px" }}
              transition={{
                type: "spring",
                stiffness: 60,
                damping: 20,
                delay: i * 0.1,
              }}
              className="relative p-5 rounded-xl border border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.04)] transition-all duration-200 hover:border-[rgba(255,255,255,0.14)] hover:bg-[rgba(255,255,255,0.06)]"
            >
              <div className="flex items-center gap-2.5 mb-4">
                <div
                  className="w-2 h-2 rounded-full"
                  style={{ backgroundColor: silo.color }}
                />
                <span
                  className="text-xs font-medium"
                  style={{ color: `${silo.color}CC` }}
                >
                  {silo.provider}
                </span>
              </div>
              <p className="text-[#7E7B93] text-sm leading-relaxed font-mono">
                {silo.snippet}
              </p>

              {i < SILOS.length - 1 && (
                <div className="hidden md:flex absolute -right-2.5 top-1/2 -translate-y-1/2 z-20 items-center gap-1">
                  <div className="w-1.5 h-[1px] bg-[rgba(255,255,255,0.1)]" />
                  <div className="w-1 h-1 rounded-full bg-[#FF6B35] opacity-60" />
                  <div className="w-1.5 h-[1px] bg-[rgba(255,255,255,0.1)]" />
                </div>
              )}
            </motion.div>
          ))}
        </div>

        <motion.p
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true }}
          transition={{ duration: 0.8, delay: 0.4 }}
          className="text-center text-[#5D5A73] mt-10 text-sm"
        >
          Your research, preferences, and context -- trapped in silos.
        </motion.p>
      </div>
    </section>
  );
}
