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
          code({ className, children, ...props }) {
            // react-markdown v10 no longer passes an `inline` prop --
            // a language- className only ever appears on fenced block
            // code (```python), never on single-backtick inline code,
            // so its presence is what actually distinguishes them now.
            const match = /language-(\w+)/.exec(className || '')
            if (!match) {
              return <code className="inline-code" {...props}>{children}</code>
            }
            return (
              <SyntaxHighlighter
                style={oneDark}
                language={match[1]}
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
