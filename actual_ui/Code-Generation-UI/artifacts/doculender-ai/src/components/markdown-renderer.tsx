import { Children, cloneElement, isValidElement, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export function renderMarkdownWithReasoning(content?: string) {
  const safeContent = typeof content === 'string' ? content : '';
  const parts = safeContent.split(/(\[reasoning\][\s\S]*?\[\/reasoning\])/);

  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith('[reasoning]') && part.endsWith('[/reasoning]')) {
          const reasoning = part.slice(11, -12);
          return <div key={i} className="my-1 border-l-2 border-[#6b7280] pl-2 text-sm italic text-[#9ca3af]">{reasoning}</div>;
        }

        return (
          <ReactMarkdown key={i} remarkPlugins={[remarkGfm]} components={documentMarkdownComponents}>
            {part}
          </ReactMarkdown>
        );
      })}
    </>
  );
}

export function withCitationBadges(node: ReactNode, keyPrefix = 'c'): ReactNode {
  if (typeof node === 'string') {
    const output: ReactNode[] = [];
    const regex = /\[(\d+)\]/g;
    let cursor = 0;
    let match: RegExpExecArray | null;

    while ((match = regex.exec(node)) !== null) {
      const preceding = node.slice(cursor, match.index).replace(/\s+$/, '');
      if (preceding) output.push(preceding);

      const isHeadingCitation = /^t?h[123]/.test(keyPrefix);
      const citationClass = isHeadingCitation
        ? 'relative -top-[0.46em] mx-[1px] inline-flex items-center justify-center rounded-[3px] border border-[#d6c39f] bg-[#efe2c7] px-[3px] py-0 align-baseline font-mono text-[8px] font-semibold leading-none text-[#7a5b26]'
        : 'relative -top-[0.48em] mx-[1px] inline-flex items-center justify-center rounded-[3px] border border-[#d6c39f] bg-[#efe2c7] px-[4px] py-0 align-baseline font-mono text-[9px] font-semibold leading-none text-[#7a5b26]';

      output.push(
        <sup key={`${keyPrefix}-${match.index}`} className={citationClass}>
          {match[1]}
        </sup>,
      );
      cursor = regex.lastIndex;
    }

    const tail = node.slice(cursor);
    if (tail) output.push(tail);
    return output;
  }

  if (Array.isArray(node)) return node.map((child, idx) => withCitationBadges(child, `${keyPrefix}-${idx}`));

  if (isValidElement<{ children?: ReactNode }>(node)) {
    const children = node.props.children;
    return cloneElement(node, { children: withCitationBadges(children, `${keyPrefix}-n`) });
  }

  return node;
}

export const documentMarkdownComponents = {
  h1: ({ children }: { children?: ReactNode }) => <h1 className="mt-4 text-2xl font-semibold tracking-[-0.01em] text-[#1f2933]">{withCitationBadges(children, 'h1')}</h1>,
  h2: ({ children }: { children?: ReactNode }) => <h2 className="mt-4 text-xl font-semibold tracking-[-0.01em] text-[#24323d]">{withCitationBadges(children, 'h2')}</h2>,
  h3: ({ children }: { children?: ReactNode }) => <h3 className="mt-3 text-base font-semibold text-[#2b3b46]">{withCitationBadges(children, 'h3')}</h3>,
  p: ({ children }: { children?: ReactNode }) => <p className="my-3 text-[14px] leading-7 text-[#2c3740]">{withCitationBadges(children, 'p')}</p>,
  li: ({ children }: { children?: ReactNode }) => <li className="my-1.5 ml-4 list-disc text-[14px] leading-7 text-[#2c3740]">{withCitationBadges(children, 'li')}</li>,
  blockquote: ({ children }: { children?: ReactNode }) => <blockquote className="my-3 rounded-r border-l-4 border-[#c7b085] bg-[#f2e8d4] px-3 py-2 text-[13px] italic text-[#6b5632]">{withCitationBadges(children, 'bq')}</blockquote>,
  hr: () => <hr className="my-5 border-[#e5d8bf]" />,
  code: ({ children }: { children?: ReactNode }) => <code className="rounded bg-[#efe8d8] px-1.5 py-0.5 font-mono text-[12px] text-[#6a5735]">{Children.toArray(children)}</code>,
};

export const terminalMarkdownComponents = {
  h1: ({ children }: { children?: ReactNode }) => <h1 className="mt-3 text-lg font-semibold text-[#f3efe3]">{withCitationBadges(children, 'th1')}</h1>,
  h2: ({ children }: { children?: ReactNode }) => <h2 className="mt-2 text-base font-semibold text-[#efe8d7]">{withCitationBadges(children, 'th2')}</h2>,
  h3: ({ children }: { children?: ReactNode }) => <h3 className="mt-2 text-sm font-semibold text-[#e8dfcc]">{withCitationBadges(children, 'th3')}</h3>,
  p: ({ children }: { children?: ReactNode }) => <p className="my-1.5 font-sans text-[13px] leading-6 text-[#dfe3df]">{withCitationBadges(children, 'tp')}</p>,
  li: ({ children }: { children?: ReactNode }) => <li className="my-1 ml-4 list-disc font-sans text-[13px] leading-6 text-[#dfe3df]">{withCitationBadges(children, 'tli')}</li>,
  blockquote: ({ children }: { children?: ReactNode }) => <blockquote className="my-2 border-l-2 border-[#6f7f86] pl-3 text-[12px] italic text-[#b9c4c8]">{withCitationBadges(children, 'tbq')}</blockquote>,
  code: ({ children }: { children?: ReactNode }) => <code className="rounded bg-[#33424a] px-1 py-0.5 font-mono text-[11px] text-[#f2f4f0]">{Children.toArray(children)}</code>,
};
