import React, { useState } from 'react';

export default function RecoveryWizard() {
  const [drive, setDrive] = useState('\\\\.\\C:');
  const [scanMode, setScanMode] = useState('Deep');
  const [isScanning, setIsScanning] = useState(false);
  const [results, setResults] = useState([]);
  const [error, setError] = useState(null);

  const handleStartScan = async () => {
    setIsScanning(true);
    setError(null);
    try {
      // Fetch validated data from Member 2's Python backend
      const response = await fetch('http://localhost:8000/api/scan/results');
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data = await response.json();
      setResults(data);
    } catch (err) {
      setError('Failed to fetch scan results. Please ensure the Python API is running.');
      console.error(err);
    } finally {
      setIsScanning(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-8 font-sans">
      <div className="max-w-5xl mx-auto bg-slate-900 rounded-2xl shadow-2xl overflow-hidden border border-slate-800">
        
        {/* Header */}
        <div className="p-8 border-b border-slate-800 bg-slate-900/50">
          <h1 className="text-3xl font-extrabold text-white mb-2 tracking-tight">Recovery Wizard</h1>
          <p className="text-slate-400">Configure parameters to begin sector-level file carving.</p>
        </div>
        
        {/* Controls */}
        <div className="p-8 space-y-8">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            <div>
              <label className="block text-sm font-semibold text-slate-300 mb-3">Target Logical/Physical Drive</label>
              <select 
                value={drive} 
                onChange={(e) => setDrive(e.target.value)}
                className="w-full bg-slate-950 border border-slate-700 rounded-xl px-4 py-3.5 text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500 transition-shadow appearance-none"
              >
                <option value="\\\\.\\C:">Local Disk (C:)</option>
                <option value="\\\\.\\D:">Local Disk (D:)</option>
                <option value="\\\\.\\PhysicalDrive0">PhysicalDrive0 (Raw Device)</option>
              </select>
            </div>
            
            <div>
              <label className="block text-sm font-semibold text-slate-300 mb-3">Recovery Scan Mode</label>
              <select 
                value={scanMode} 
                onChange={(e) => setScanMode(e.target.value)}
                className="w-full bg-slate-950 border border-slate-700 rounded-xl px-4 py-3.5 text-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500 transition-shadow appearance-none"
              >
                <option value="Quick">Quick Scan (MFT Parse)</option>
                <option value="Deep">Deep Scan (Magic Bytes Carving)</option>
              </select>
            </div>
          </div>

          <button 
            onClick={handleStartScan}
            disabled={isScanning}
            className={`w-full py-4 rounded-xl font-bold text-lg shadow-lg transition-all ${
              isScanning 
                ? 'bg-indigo-600/50 text-indigo-300 cursor-not-allowed' 
                : 'bg-indigo-600 hover:bg-indigo-500 text-white hover:shadow-indigo-500/25 hover:-translate-y-0.5'
            }`}
          >
            {isScanning ? 'Carving Sectors...' : 'Start Data Recovery Scan'}
          </button>
          
          {error && (
            <div className="p-4 bg-red-950/30 border border-red-900 rounded-xl text-red-400 font-medium">
              {error}
            </div>
          )}
        </div>

        {/* Results Dashboard */}
        {results.length > 0 && (
          <div className="p-8 bg-slate-900 border-t border-slate-800">
            <h2 className="text-xl font-bold mb-6 text-slate-200">Discovered Candidates ({results.length})</h2>
            <div className="overflow-hidden rounded-xl border border-slate-800">
              <table className="w-full text-left border-collapse bg-slate-950/50">
                <thead>
                  <tr className="border-b border-slate-800 text-slate-400 text-xs uppercase tracking-wider bg-slate-900/50">
                    <th className="py-4 px-6 font-semibold">ID</th>
                    <th className="py-4 px-6 font-semibold">Format</th>
                    <th className="py-4 px-6 font-semibold">Suggested Naming</th>
                    <th className="py-4 px-6 font-semibold">Sector Offset</th>
                    <th className="py-4 px-6 font-semibold">Confidence</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/50">
                  {results.map((result) => (
                    <tr key={result.id} className="hover:bg-slate-800/30 transition-colors">
                      <td className="py-4 px-6 text-slate-500 font-mono text-sm">#{result.id}</td>
                      <td className="py-4 px-6">
                        <span className={`px-2.5 py-1 rounded-md text-xs font-bold tracking-wide ${
                          result.type === 'PDF' ? 'bg-rose-950/50 text-rose-400 border border-rose-900' 
                                                : 'bg-sky-950/50 text-sky-400 border border-sky-900'
                        }`}>
                          {result.type}
                        </span>
                      </td>
                      <td className="py-4 px-6 font-medium text-slate-300">{result.suggested_name}</td>
                      <td className="py-4 px-6 text-slate-400 font-mono text-sm">
                        0x{result.offset.toString(16).toUpperCase().padStart(8, '0')}
                      </td>
                      <td className="py-4 px-6">
                        <div className="flex items-center gap-2">
                          <div className={`h-2 w-2 rounded-full ${
                            result.confidence === 'High' ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.5)]' 
                            : result.confidence === 'Medium' ? 'bg-amber-500 shadow-[0_0_8px_rgba(245,158,11,0.5)]' 
                            : 'bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.5)]'
                          }`}></div>
                          <span className="text-sm font-medium text-slate-300">{result.confidence}</span>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
