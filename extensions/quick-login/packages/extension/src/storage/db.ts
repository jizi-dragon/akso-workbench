import { IDB_NAME, IDB_STORE_ACCOUNTS, IDB_STORE_SESSIONS, IDB_VERSION } from '../shared/constants';
import type { ParallelAccount, Session } from '../shared/types';

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(IDB_NAME, IDB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(IDB_STORE_SESSIONS)) {
        db.createObjectStore(IDB_STORE_SESSIONS, { keyPath: 'id' });
      }
      // v2：并行账号存储（纯扩展多账号模式）
      if (!db.objectStoreNames.contains(IDB_STORE_ACCOUNTS)) {
        db.createObjectStore(IDB_STORE_ACCOUNTS, { keyPath: 'id' });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function tx<T>(store: string, mode: IDBTransactionMode, run: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return openDb().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const t = db.transaction(store, mode);
        const req = run(t.objectStore(store));
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      }),
  );
}

export const db = {
  sessions: {
    async list(): Promise<Session[]> {
      return tx<Session[]>(IDB_STORE_SESSIONS, 'readonly', (s) => s.getAll() as IDBRequest<Session[]>);
    },
    async get(id: string): Promise<Session | undefined> {
      return tx<Session | undefined>(IDB_STORE_SESSIONS, 'readonly', (s) => s.get(id) as IDBRequest<Session | undefined>);
    },
    async put(session: Session): Promise<void> {
      await tx(IDB_STORE_SESSIONS, 'readwrite', (s) => s.put(session));
    },
    async delete(id: string): Promise<void> {
      await tx(IDB_STORE_SESSIONS, 'readwrite', (s) => s.delete(id));
    },
  },
  accounts: {
    async list(): Promise<ParallelAccount[]> {
      return tx<ParallelAccount[]>(IDB_STORE_ACCOUNTS, 'readonly', (s) => s.getAll() as IDBRequest<ParallelAccount[]>);
    },
    async get(id: string): Promise<ParallelAccount | undefined> {
      return tx<ParallelAccount | undefined>(IDB_STORE_ACCOUNTS, 'readonly', (s) => s.get(id) as IDBRequest<ParallelAccount | undefined>);
    },
    async put(account: ParallelAccount): Promise<void> {
      await tx(IDB_STORE_ACCOUNTS, 'readwrite', (s) => s.put(account));
    },
    async delete(id: string): Promise<void> {
      await tx(IDB_STORE_ACCOUNTS, 'readwrite', (s) => s.delete(id));
    },
  },
};
