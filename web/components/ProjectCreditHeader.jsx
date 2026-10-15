'use client';

export default function ProjectCreditHeader() {
  return (
    <div className="bg-[#090d1a] text-slate-200 border-b border-slate-800/80 px-4 py-2.5 text-xs font-medium tracking-wide">
      <div className="mx-auto max-w-7xl flex flex-col md:flex-row items-center justify-between gap-2.5 text-center md:text-left">
        <div className="flex items-center gap-2">
          <span className="text-slate-400 uppercase tracking-wider text-[11px] font-semibold">
            Project done under:
          </span>
          <span className="font-bold text-white bg-slate-800/90 px-2.5 py-0.5 rounded border border-slate-700/70 shadow-sm">
            Prof. Jaidhar C D
          </span>
        </div>
        <div className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-slate-300">
          <span className="text-slate-400 uppercase tracking-wider text-[11px] font-semibold">
            Project done by:
          </span>
          <span className="text-white font-medium">
            Pranav V Pogal <span className="text-brand-300 font-mono text-[11px]">(241IT055)</span>
          </span>
          <span className="text-slate-600 hidden sm:inline">•</span>
          <span className="text-white font-medium">
            Nikhil Soni <span className="text-brand-300 font-mono text-[11px]">(241IT048)</span>
          </span>
          <span className="text-slate-600 hidden sm:inline">•</span>
          <span className="text-white font-medium">
            Shubh Thakkar <span className="text-brand-300 font-mono text-[11px]">(241IT076)</span>
          </span>
        </div>
      </div>
    </div>
  );
}
