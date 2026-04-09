/**
 * 文件浏览器页面
 *
 * 提供三个 Tab：
 * 1. 工作区文件：浏览和查看 workspace 目录下的文件
 * 2. 备份文件：查看 .backups/ 目录中的文件备份
 * 3. 恢复快照：查看 .snapshots/ 目录中的恢复快照
 */

import { useState, useEffect } from 'react';
import { listFiles, readFileContent, listSnapshots, getSnapshot, listBackups, readBackupContent } from '../api/client';

/** 文件信息接口 */
interface FileItem {
  path: string;
  isDir: boolean;
  size: number;
  mtime?: number;
}

/** 文件内容接口 */
interface FileData {
  content: string;
  size: number;
  lines: number;
  lang: string;
  mtime: number;
}

/**
 * 文件浏览器页面主组件
 */
export function FileBrowserPage() {
  // 当前激活的 Tab: workspace | backups | snapshots
  const [activeTab, setActiveTab] = useState('workspace');

  return (
    <>
      <div className="page-header">
        <h2>文件浏览器</h2>
        <p>浏览工作区文件、备份和恢复快照</p>
      </div>

      <div className="page-content">
        {/* Tab 切换 */}
        <div className="tabs">
          <button
            className={`tab ${activeTab === 'workspace' ? 'active' : ''}`}
            onClick={() => setActiveTab('workspace')}
          >
            工作区文件
          </button>
          <button
            className={`tab ${activeTab === 'backups' ? 'active' : ''}`}
            onClick={() => setActiveTab('backups')}
          >
            备份文件
          </button>
          <button
            className={`tab ${activeTab === 'snapshots' ? 'active' : ''}`}
            onClick={() => setActiveTab('snapshots')}
          >
            恢复快照
          </button>
        </div>

        {/* 根据 Tab 渲染不同内容 */}
        {activeTab === 'workspace' && <WorkspaceFilesTab />}
        {activeTab === 'backups' && <BackupsTab />}
        {activeTab === 'snapshots' && <SnapshotsTab />}
      </div>
    </>
  );
}

/**
 * 工作区文件 Tab
 * 左侧文件树，右侧文件查看器
 */
function WorkspaceFilesTab() {
  // 文件列表
  const [files, setFiles] = useState<FileItem[]>([]);
  // 加载状态
  const [loading, setLoading] = useState(true);
  // 当前选中的文件路径
  const [selectedFile, setSelectedFile] = useState('');
  // 文件内容
  const [fileContent, setFileContent] = useState<FileData | null>(null);
  // 读取文件内容的加载状态
  const [fileLoading, setFileLoading] = useState(false);

  /** 加载文件列表 */
  async function loadFiles() {
    try {
      setLoading(true);
      const result = await listFiles();
      setFiles(result.files);
    } catch (e) {
      // 加载失败，设为空
      setFiles([]);
    } finally {
      setLoading(false);
    }
  }

  // 初始加载
  useEffect(() => {
    loadFiles();
  }, []);

  /**
   * 点击选择文件时，读取文件内容
   *
   * @param filePath 文件相对路径
   */
  async function handleSelectFile(filePath: string) {
    setSelectedFile(filePath);
    setFileContent(null);

    try {
      setFileLoading(true);
      const data = await readFileContent(filePath);
      setFileContent(data);
    } catch (e) {
      // 读取失败时清空内容
      setFileContent({ content: '读取失败', size: 0, lines: 0, lang: 'text', mtime: 0 });
    } finally {
      setFileLoading(false);
    }
  }

  /** 格式化文件大小 */
  function formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  /** 格式化时间戳 */
  function formatTime(ts?: number): string {
    if (!ts) return '-';
    return new Date(ts * 1000).toLocaleString('zh-CN');
  }

  return (
    <div className="file-browser-layout">
      {/* 左侧文件树 */}
      <div className="file-tree">
        <div className="file-tree-header">工作区文件</div>

        {loading && (
          <div className="loading">
            <div className="spinner" />
            加载中...
          </div>
        )}

        {!loading && files.length === 0 && (
          <div className="empty-state">
            <div className="placeholder-icon">~</div>
            <p>工作区暂无文件</p>
          </div>
        )}

        {!loading && files.length > 0 && (
          files.map((f) => (
            <div
              key={f.path}
              className={`file-tree-item ${selectedFile === f.path ? 'active' : ''}`}
              onClick={() => !f.isDir && handleSelectFile(f.path)}
              style={{ cursor: f.isDir ? 'default' : 'pointer' }}
            >
              <span>{f.isDir ? '[D]' : '[F]'}</span>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {f.path}
              </span>
              {!f.isDir && (
                <span className="file-type">{formatSize(f.size)}</span>
              )}
            </div>
          ))
        )}
      </div>

      {/* 右侧文件查看器 */}
      <div className="file-viewer">
        {fileContent ? (
          <>
            <div className="file-viewer-header">
              <span className="file-name">{selectedFile}</span>
              <span className="file-meta">
                {formatSize(fileContent.size)} / {fileContent.lines} 行 / {fileContent.lang}
              </span>
            </div>
            <div className="file-viewer-content">
              {fileLoading ? (
                <div className="loading">
                  <div className="spinner" />
                  读取中...
                </div>
              ) : (
                <pre>{fileContent.content}</pre>
              )}
            </div>
          </>
        ) : (
          <div className="empty-state">
            <div className="placeholder-icon">~</div>
            <p>请选择一个文件以查看内容</p>
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * 备份文件 Tab
 * 显示备份文件列表，点击可查看内容
 */
function BackupsTab() {
  interface BackupItem {
    name: string;
    size: number;
    mtime: number;
  }

  const [backups, setBackups] = useState<BackupItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedBackup, setSelectedBackup] = useState('');
  const [backupContent, setBackupContent] = useState('');

  /** 加载备份列表 */
  async function loadBackups() {
    try {
      setLoading(true);
      const result = await listBackups();
      setBackups(result.backups);
    } catch {
      setBackups([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadBackups();
  }, []);

  /** 选择备份文件时读取内容 */
  async function handleSelectBackup(name: string) {
    setSelectedBackup(name);
    setBackupContent('');
    try {
      const data = await readBackupContent(name);
      setBackupContent(data.content || '');
    } catch {
      setBackupContent('读取失败');
    }
  }

  function formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function formatTime(ts: number): string {
    return new Date(ts * 1000).toLocaleString('zh-CN');
  }

  return (
    <div className="file-browser-layout">
      <div className="file-tree">
        <div className="file-tree-header">备份文件</div>

        {loading && (
          <div className="loading">
            <div className="spinner" />
            加载中...
          </div>
        )}

        {!loading && backups.length === 0 && (
          <div className="empty-state">
            <div className="placeholder-icon">~</div>
            <p>暂无备份文件</p>
          </div>
        )}

        {!loading && backups.length > 0 && backups.map((b) => (
          <div
            key={b.name}
            className={`file-tree-item ${selectedBackup === b.name ? 'active' : ''}`}
            onClick={() => handleSelectBackup(b.name)}
          >
            <span>{b.name}</span>
            <span className="file-type">{formatSize(b.size)}</span>
          </div>
        ))}
      </div>

      <div className="file-viewer">
        {backupContent ? (
          <>
            <div className="file-viewer-header">
              <span className="file-name">{selectedBackup}</span>
            </div>
            <div className="file-viewer-content">
              <pre>{backupContent}</pre>
            </div>
          </>
        ) : (
          <div className="empty-state">
            <div className="placeholder-icon">~</div>
            <p>请选择一个备份文件以查看内容</p>
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * 恢复快照 Tab
 * 显示恢复快照列表，点击可查看快照详情和文件内容
 */
function SnapshotsTab() {
  interface SnapshotItem {
    id: string;
    timestamp: string;
    reason: string;
    activeFiles: string[];
  }

  interface SnapshotDetail {
    metadata: Record<string, any>;
    conversationSummary: string;
    codeFiles: Array<{ path: string; content: string }>;
  }

  const [snapshots, setSnapshots] = useState<SnapshotItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedSnapshot, setSelectedSnapshot] = useState<SnapshotDetail | null>(null);
  const [selectedSnapshotId, setSelectedSnapshotId] = useState<string | null>(null);
  const [selectedCodeFile, setSelectedCodeFile] = useState('');

  /** 加载快照列表 */
  async function loadSnapshots() {
    try {
      setLoading(true);
      const result = await listSnapshots();
      setSnapshots(result.snapshots);
    } catch {
      setSnapshots([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadSnapshots();
  }, []);

  /** 选择快照时获取详情 */
  async function handleSelectSnapshot(snapshotId: string) {
    setSelectedCodeFile('');
    try {
      const detail = await getSnapshot(snapshotId);
      setSelectedSnapshot(detail);
      setSelectedSnapshotId(snapshotId);
    } catch {
      setSelectedSnapshot(null);
      setSelectedSnapshotId(null);
    }
  }

  return (
    <div className="file-browser-layout">
      <div className="file-tree">
        <div className="file-tree-header">恢复快照</div>

        {loading && (
          <div className="loading">
            <div className="spinner" />
            加载中...
          </div>
        )}

        {!loading && snapshots.length === 0 && (
          <div className="empty-state">
            <div className="placeholder-icon">~</div>
            <p>暂无恢复快照</p>
          </div>
        )}

        {!loading && snapshots.length > 0 && snapshots.map((s) => (
          <div
            key={s.id}
            className={`file-tree-item ${selectedSnapshotId === s.id ? 'active' : ''}`}
            onClick={() => handleSelectSnapshot(s.id)}
          >
            <span>{s.id.slice(0, 20)}...</span>
          </div>
        ))}
      </div>

      <div className="file-viewer">
        {selectedSnapshot ? (
          <>
            <div className="file-viewer-header">
              <span className="file-name">快照详情</span>
              <span className="file-meta">
                创建原因: {(selectedSnapshot.metadata as any).reason || '-'}
              </span>
            </div>
            <div className="file-viewer-content" style={{ padding: '1rem' }}>
              {/* 活跃文件列表 */}
              {selectedSnapshot.codeFiles.length > 0 && (
                <>
                  <div style={{ marginBottom: '1rem' }}>
                    <strong style={{ fontSize: '0.85rem' }}>包含文件：</strong>
                    <ul style={{ fontSize: '0.8rem', marginTop: '0.5rem' }}>
                      {selectedSnapshot.codeFiles.map((f: any, i: number) => (
                        <li
                          key={i}
                          style={{
                            cursor: 'pointer',
                            color: selectedCodeFile === f.path ? '#4f46e5' : '#64748b',
                          }}
                          onClick={() => setSelectedCodeFile(f.path)}
                        >
                          {f.path}
                        </li>
                      ))}
                    </ul>
                  </div>

                  {/* 代码内容 */}
                  {selectedCodeFile && (
                    <div>
                      <div style={{ fontWeight: 600, fontSize: '0.85rem', marginBottom: '0.5rem' }}>
                        {selectedCodeFile}
                      </div>
                      <pre style={{
                        background: '#1e293b',
                        color: '#e2e8f0',
                        padding: '1rem',
                        borderRadius: '6px',
                        overflowX: 'auto',
                        fontSize: '0.8rem',
                      }}>
                        {selectedSnapshot.codeFiles.find((f: any) => f.path === selectedCodeFile)?.content || ''}
                      </pre>
                    </div>
                  )}
                </>
              )}

              {selectedSnapshot.codeFiles.length === 0 && (
                <div className="empty-state">
                  <div className="placeholder-icon">~</div>
                  <p>此快照不包含代码文件</p>
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="empty-state">
            <div className="placeholder-icon">~</div>
            <p>请选择一个快照以查看详情</p>
          </div>
        )}
      </div>
    </div>
  );
}
