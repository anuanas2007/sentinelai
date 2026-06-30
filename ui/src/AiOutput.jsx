import ReactMarkdown from 'react-markdown'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'

/**
 * The AI's output is already markdown (prose + ```diff / ```python code
 * fences) -- this just renders it as such instead of dumping everything
 * into one plain <pre> block where code and prose look identical.
 */
export function AiOutput({ text }) {
  if (!text) return null
  return (
    <div className="ai-output">
      <ReactMarkdown
        components={{
          code({ inline, className, children, ...props }) {
            const match = /language-(\w+)/.exec(className || '')
            if (inline) {
              return <code className="inline-code" {...props}>{children}</code>
            }
            return (
              <SyntaxHighlighter
                style={oneDark}
                language={match ? match[1] : 'text'}
                PreTag="div"
                customStyle={{ borderRadius: '6px', fontSize: '12px', margin: '8px 0' }}
              >
                {String(children).replace(/\n$/, '')}
              </SyntaxHighlighter>
            )
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}
