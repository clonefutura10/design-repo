import React, { useCallback, useRef, useState } from 'react';

interface Props {
  onUpload: (file: File) => void;
}

export default function UploadView({ onUpload }: Props) {
  const [dragging, setDragging] = useState(false);
  const [selected, setSelected] = useState<File | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const accept = (file: File) => {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      alert('Only PDF files are accepted.');
      return;
    }
    setSelected(file);
  };

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) accept(file);
  }, []);

  const onDragOver = (e: React.DragEvent) => { e.preventDefault(); setDragging(true); };
  const onDragLeave = () => setDragging(false);
  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) accept(file);
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-[70vh] gap-8">
      {/* Branding strip */}
      <div className="text-center">
        <h1 className="text-3xl font-bold text-slate-800">aCRF Annotation Engine</h1>
        <p className="text-slate-500 mt-2 text-sm">
          Upload a blank CRF PDF — automatic SDTM variable annotation, colour-coded by domain class
        </p>
      </div>

      {/* Drop zone */}
      <div
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onClick={() => inputRef.current?.click()}
        className={`w-full max-w-lg cursor-pointer rounded-2xl border-2 border-dashed transition-all duration-150 p-10 flex flex-col items-center gap-4
          ${dragging
            ? 'border-blue-500 bg-blue-50 scale-[1.02]'
            : selected
              ? 'border-green-400 bg-green-50'
              : 'border-slate-300 bg-white hover:border-blue-400 hover:bg-blue-50/30'}`}
      >
        <input ref={inputRef} type="file" accept=".pdf" className="hidden" onChange={onFileChange} />

        {selected ? (
          <>
            <div className="w-14 h-14 rounded-full bg-green-100 flex items-center justify-center text-green-600 text-2xl">✓</div>
            <div className="text-center">
              <p className="font-semibold text-slate-800 text-sm">{selected.name}</p>
              <p className="text-slate-400 text-xs mt-1">{(selected.size / 1024 / 1024).toFixed(1)} MB — click to change</p>
            </div>
          </>
        ) : (
          <>
            <div className="w-14 h-14 rounded-full bg-slate-100 flex items-center justify-center text-slate-400 text-3xl">📄</div>
            <div className="text-center">
              <p className="font-medium text-slate-600">Drag & drop or click to select</p>
              <p className="text-slate-400 text-xs mt-1">Blank CRF PDF only — up to 150 MB</p>
            </div>
          </>
        )}
      </div>

      {/* Annotate button */}
      <button
        onClick={() => selected && onUpload(selected)}
        disabled={!selected}
        className={`px-8 py-3 rounded-xl font-semibold text-sm transition-all duration-150
          ${selected
            ? 'bg-blue-700 text-white hover:bg-blue-800 shadow-md hover:shadow-lg'
            : 'bg-slate-200 text-slate-400 cursor-not-allowed'}`}
      >
        Annotate CRF →
      </button>

      {/* Feature chips */}
      <div className="flex flex-wrap justify-center gap-2 text-xs text-slate-500 max-w-lg">
        {['Domain colour-coding', 'Multi-variable fields', 'Leader tick lines', 'Bookmarks', 'Legend page', 'Editable annotations'].map(f => (
          <span key={f} className="border border-slate-200 rounded-full px-3 py-1 bg-white">{f}</span>
        ))}
      </div>
    </div>
  );
}
