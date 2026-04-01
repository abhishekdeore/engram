import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Engram -- Persistent memory across every LLM",
  description:
    "Store your LLM conversations verbatim. Ask any AI to recall what you discussed with another. Word for word. Zero hallucination.",
  openGraph: {
    title: "Engram -- Persistent memory across every LLM",
    description:
      "Store your LLM conversations verbatim. Ask any AI to recall what you discussed with another. Word for word. Zero hallucination.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable}`}>
      <body className="bg-[#030014] text-[#E2DFD6] font-sans antialiased overflow-x-hidden">
        {children}
      </body>
    </html>
  );
}
