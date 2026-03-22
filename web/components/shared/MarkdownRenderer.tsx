"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface Props {
  content: string;
  className?: string;
}

export default function MarkdownRenderer({ content, className = "" }: Props) {
  return (
    <div className={`prose prose-sm max-w-none ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Tables
          table: ({ children }) => (
            <div className="overflow-x-auto my-2">
              <table className="min-w-full border-collapse text-sm">{children}</table>
            </div>
          ),
          thead: ({ children }) => (
            <thead className="bg-gray-100">{children}</thead>
          ),
          th: ({ children }) => (
            <th className="border border-gray-300 px-3 py-1 text-left font-semibold text-gray-700">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-gray-300 px-3 py-1 text-gray-800">
              {children}
            </td>
          ),
          // Code blocks
          code: ({ children, className: codeClass }) => {
            const isBlock = codeClass?.startsWith("language-");
            if (isBlock) {
              return (
                <pre className="bg-gray-800 text-gray-100 rounded-lg p-3 overflow-x-auto my-2 text-xs">
                  <code>{children}</code>
                </pre>
              );
            }
            return (
              <code className="bg-gray-100 text-gray-800 rounded px-1 py-0.5 text-xs font-mono">
                {children}
              </code>
            );
          },
          pre: ({ children }) => <>{children}</>,
          // Lists
          ul: ({ children }) => (
            <ul className="list-disc list-inside space-y-1 my-1 text-gray-800">
              {children}
            </ul>
          ),
          ol: ({ children }) => (
            <ol className="list-decimal list-inside space-y-1 my-1 text-gray-800">
              {children}
            </ol>
          ),
          li: ({ children }) => <li className="text-gray-800">{children}</li>,
          // Headings
          h1: ({ children }) => (
            <h1 className="text-lg font-bold text-gray-900 mt-3 mb-1">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-base font-bold text-gray-900 mt-2 mb-1">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-sm font-semibold text-gray-900 mt-2 mb-1">{children}</h3>
          ),
          // Paragraph
          p: ({ children }) => (
            <p className="text-gray-800 my-1 leading-relaxed">{children}</p>
          ),
          // Blockquote
          blockquote: ({ children }) => (
            <blockquote className="border-l-4 border-gray-300 pl-3 my-2 text-gray-600 italic">
              {children}
            </blockquote>
          ),
          // Strong / em
          strong: ({ children }) => (
            <strong className="font-semibold text-gray-900">{children}</strong>
          ),
          em: ({ children }) => <em className="italic text-gray-700">{children}</em>,
          // Horizontal rule
          hr: () => <hr className="border-gray-200 my-2" />,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
