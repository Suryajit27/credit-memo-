import { createContext, useContext, useState, type ReactNode } from 'react';

type RequestIdContextValue = {
  requestId: string;
  setRequestId: (id: string) => void;
};

const RequestIdContext = createContext<RequestIdContextValue>({ requestId: '', setRequestId: () => {} });

export function RequestIdProvider({ children }: { children: ReactNode }) {
  const [requestId, setRequestId] = useState('');
  return <RequestIdContext.Provider value={{ requestId, setRequestId }}>{children}</RequestIdContext.Provider>;
}

export function useRequestId() {
  return useContext(RequestIdContext);
}
