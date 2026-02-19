import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../lib/api';
import ObservabilityPage from './ObservabilityPage';

vi.mock('../../lib/api', () => ({
  cancelIndexJob: vi.fn(),
  extractApiError: vi.fn((error, fallback = 'Request failed') => error?.message || fallback),
  getIndexJob: vi.fn(),
  getObservabilitySummary: vi.fn(),
  retryIndexJob: vi.fn(),
  runObservabilitySearch: vi.fn(),
  triggerIndexRebuild: vi.fn(),
  triggerMemoryReindex: vi.fn(),
  triggerSleepConsolidation: vi.fn(),
}));

const buildSummary = ({
  activeJobId = null,
  recentJobs = [],
  timestamp = '2026-01-01T00:00:00Z',
  queueDepth = recentJobs.length,
  lastError = null,
} = {}) => ({
  status: 'ok',
  timestamp,
  search_stats: {},
  health: {
    index: { degraded: false },
    runtime: {
      index_worker: {
        active_job_id: activeJobId,
        recent_jobs: recentJobs,
        queue_depth: queueDepth,
        cancelling_jobs: 0,
        sleep_pending: false,
        last_error: lastError,
      },
      sleep_consolidation: {},
    },
  },
  index_latency: {},
  cleanup_query_stats: {},
});

describe('ObservabilityPage', () => {
  const getJobCardById = async (jobId) => {
    const jobLabel = await screen.findByText(jobId);
    const card = jobLabel.closest('article');
    expect(card).not.toBeNull();
    return card;
  };

  beforeEach(() => {
    vi.clearAllMocks();
    api.getObservabilitySummary.mockResolvedValue(buildSummary());
    api.getIndexJob.mockResolvedValue({ job: null });
    api.retryIndexJob.mockResolvedValue({ job_id: 'retry-default' });
    api.runObservabilitySearch.mockResolvedValue({ results: [] });
    api.triggerIndexRebuild.mockResolvedValue({ job_id: 'rebuild-default' });
    api.triggerMemoryReindex.mockResolvedValue({ job_id: 'reindex-default' });
    api.triggerSleepConsolidation.mockResolvedValue({ job_id: 'sleep-default' });
    api.cancelIndexJob.mockResolvedValue({});
  });

  it('uses unified retry endpoint when retry API is available', async () => {
    const failedJob = {
      job_id: 'job-unified',
      status: 'failed',
      task_type: 'reindex_memory',
      memory_id: 12,
      reason: 'failed-job',
    };
    api.getObservabilitySummary
      .mockResolvedValueOnce(buildSummary({ recentJobs: [failedJob], timestamp: '2026-01-01T00:00:00Z' }))
      .mockResolvedValueOnce(buildSummary({ recentJobs: [failedJob], timestamp: '2026-01-01T00:00:01Z' }));
    api.retryIndexJob.mockResolvedValueOnce({ job_id: 'job-unified-retry' });

    const user = userEvent.setup();
    render(<ObservabilityPage />);

    const jobCard = await getJobCardById('job-unified');
    await user.click(within(jobCard).getByRole('button', { name: 'Retry' }));

    await waitFor(() => {
      expect(api.retryIndexJob).toHaveBeenCalledWith('job-unified', { reason: 'retry:job-unified' });
    });
    expect(api.triggerMemoryReindex).not.toHaveBeenCalled();
    expect(api.triggerIndexRebuild).not.toHaveBeenCalled();
    expect(api.triggerSleepConsolidation).not.toHaveBeenCalled();
    expect(await screen.findByText(/Retry requested/i)).toBeInTheDocument();
  });

  it('falls back to old backend endpoint when retry API is unsupported', async () => {
    const legacyJob = {
      job_id: 'job-legacy',
      status: 'failed',
      task_type: 'reindex_memory',
      memory_id: 77,
      reason: 'legacy-backend',
    };
    api.getObservabilitySummary
      .mockResolvedValueOnce(buildSummary({ recentJobs: [legacyJob], timestamp: '2026-01-01T00:00:00Z' }))
      .mockResolvedValueOnce(buildSummary({ recentJobs: [legacyJob], timestamp: '2026-01-01T00:00:01Z' }));
    api.retryIndexJob.mockRejectedValueOnce({
      response: { status: 404, data: { detail: 'Not Found' } },
    });
    api.triggerMemoryReindex.mockResolvedValueOnce({ job_id: 'job-legacy-retry' });

    const user = userEvent.setup();
    render(<ObservabilityPage />);

    const jobCard = await getJobCardById('job-legacy');
    await user.click(within(jobCard).getByRole('button', { name: 'Retry' }));

    await waitFor(() => {
      expect(api.retryIndexJob).toHaveBeenCalledWith('job-legacy', { reason: 'retry:job-legacy' });
      expect(api.triggerMemoryReindex).toHaveBeenCalledWith(77, { reason: 'retry:job-legacy', wait: false });
    });
    expect(api.triggerIndexRebuild).not.toHaveBeenCalled();
    expect(api.triggerSleepConsolidation).not.toHaveBeenCalled();
    expect(await screen.findByText(/Retry requested/i)).toBeInTheDocument();
  });

  it('falls back to legacy rebuild endpoint when retry endpoint returns 405', async () => {
    const legacyJob = {
      job_id: 'job-legacy-rebuild',
      status: 'failed',
      task_type: 'rebuild_index',
      reason: 'legacy-rebuild',
    };
    api.getObservabilitySummary
      .mockResolvedValueOnce(buildSummary({ recentJobs: [legacyJob], timestamp: '2026-01-01T00:00:00Z' }))
      .mockResolvedValueOnce(buildSummary({ recentJobs: [legacyJob], timestamp: '2026-01-01T00:00:01Z' }));
    api.retryIndexJob.mockRejectedValueOnce({
      response: { status: 405, data: { detail: 'Method Not Allowed' } },
    });
    api.triggerIndexRebuild.mockResolvedValueOnce({ job_id: 'job-legacy-rebuild-retry' });

    const user = userEvent.setup();
    render(<ObservabilityPage />);

    const jobCard = await getJobCardById('job-legacy-rebuild');
    await user.click(within(jobCard).getByRole('button', { name: 'Retry' }));

    await waitFor(() => {
      expect(api.retryIndexJob).toHaveBeenCalledWith('job-legacy-rebuild', { reason: 'retry:job-legacy-rebuild' });
      expect(api.triggerIndexRebuild).toHaveBeenCalledWith({ reason: 'retry:job-legacy-rebuild', wait: false });
    });
    expect(api.triggerMemoryReindex).not.toHaveBeenCalled();
    expect(api.triggerSleepConsolidation).not.toHaveBeenCalled();
    expect(await screen.findByText(/Retry requested/i)).toBeInTheDocument();
  });

  it('does not fallback to legacy endpoints when retry returns job_not_found', async () => {
    const failedJob = {
      job_id: 'job-not-found',
      status: 'failed',
      task_type: 'reindex_memory',
      memory_id: 88,
      reason: 'not-found-case',
    };
    api.getObservabilitySummary.mockResolvedValue(buildSummary({ recentJobs: [failedJob] }));
    api.retryIndexJob.mockRejectedValueOnce({
      message: 'job not found',
      response: {
        status: 404,
        data: {
          detail: { error: 'job_not_found', message: 'job not found' },
        },
      },
    });

    const user = userEvent.setup();
    render(<ObservabilityPage />);

    const jobCard = await getJobCardById('job-not-found');
    await user.click(within(jobCard).getByRole('button', { name: 'Retry' }));

    await waitFor(() => {
      expect(api.retryIndexJob).toHaveBeenCalledWith('job-not-found', { reason: 'retry:job-not-found' });
    });
    expect(api.triggerMemoryReindex).not.toHaveBeenCalled();
    expect(api.triggerIndexRebuild).not.toHaveBeenCalled();
    expect(api.triggerSleepConsolidation).not.toHaveBeenCalled();
    expect(await screen.findByText(/Retry failed \(job-not-found\): job not found/i)).toBeInTheDocument();
  });

  it('does not fallback when 404 detail message reports job not found', async () => {
    const failedJob = {
      job_id: 'job-not-found-message',
      status: 'failed',
      task_type: 'rebuild_index',
      reason: 'not-found-message-case',
    };
    api.getObservabilitySummary.mockResolvedValue(buildSummary({ recentJobs: [failedJob] }));
    api.retryIndexJob.mockRejectedValueOnce({
      message: 'job missing',
      response: {
        status: 404,
        data: {
          detail: {
            error: 'request_failed',
            reason: 'backend_error',
            message: 'job not found',
          },
        },
      },
    });

    const user = userEvent.setup();
    render(<ObservabilityPage />);

    const jobCard = await getJobCardById('job-not-found-message');
    await user.click(within(jobCard).getByRole('button', { name: 'Retry' }));

    await waitFor(() => {
      expect(api.retryIndexJob).toHaveBeenCalledWith('job-not-found-message', { reason: 'retry:job-not-found-message' });
    });
    expect(api.triggerMemoryReindex).not.toHaveBeenCalled();
    expect(api.triggerIndexRebuild).not.toHaveBeenCalled();
    expect(api.triggerSleepConsolidation).not.toHaveBeenCalled();
    expect(await screen.findByText(/Retry failed \(job-not-found-message\): job missing/i)).toBeInTheDocument();
  });

  it('falls back to legacy sleep consolidation endpoint when retry endpoint returns 405', async () => {
    const legacyJob = {
      job_id: 'job-legacy-sleep',
      status: 'failed',
      task_type: 'sleep_consolidation',
      reason: 'legacy-sleep',
    };
    api.getObservabilitySummary
      .mockResolvedValueOnce(buildSummary({ recentJobs: [legacyJob], timestamp: '2026-01-01T00:00:00Z' }))
      .mockResolvedValueOnce(buildSummary({ recentJobs: [legacyJob], timestamp: '2026-01-01T00:00:01Z' }));
    api.retryIndexJob.mockRejectedValueOnce({
      response: { status: 405, data: { detail: 'Method Not Allowed' } },
    });
    api.triggerSleepConsolidation.mockResolvedValueOnce({ job_id: 'job-legacy-sleep-retry' });

    const user = userEvent.setup();
    render(<ObservabilityPage />);

    const jobCard = await getJobCardById('job-legacy-sleep');
    await user.click(within(jobCard).getByRole('button', { name: 'Retry' }));

    await waitFor(() => {
      expect(api.retryIndexJob).toHaveBeenCalledWith('job-legacy-sleep', { reason: 'retry:job-legacy-sleep' });
      expect(api.triggerSleepConsolidation).toHaveBeenCalledWith({ reason: 'retry:job-legacy-sleep', wait: false });
    });
    expect(api.triggerMemoryReindex).not.toHaveBeenCalled();
    expect(api.triggerIndexRebuild).not.toHaveBeenCalled();
    expect(await screen.findByText(/Retry requested/i)).toBeInTheDocument();
  });

  it('shows explicit error when fallback task type is unsupported', async () => {
    const unknownTaskJob = {
      job_id: 'job-unknown-task',
      status: 'failed',
      task_type: 'unknown_task_type',
      reason: 'unknown-task',
    };
    api.getObservabilitySummary.mockResolvedValue(buildSummary({ recentJobs: [unknownTaskJob] }));
    api.retryIndexJob.mockRejectedValueOnce({
      response: { status: 405, data: { detail: 'Method Not Allowed' } },
    });

    const user = userEvent.setup();
    render(<ObservabilityPage />);

    const jobCard = await getJobCardById('job-unknown-task');
    await user.click(within(jobCard).getByRole('button', { name: 'Retry' }));

    expect(api.triggerMemoryReindex).not.toHaveBeenCalled();
    expect(api.triggerIndexRebuild).not.toHaveBeenCalled();
    expect(api.triggerSleepConsolidation).not.toHaveBeenCalled();
    expect(
      await screen.findByText(
        /Retry failed \(job-unknown-task\): retry for task type 'unknown_task_type' is not supported/i,
      ),
    ).toBeInTheDocument();
  });

  it('renders runtime queue depth and last worker error', async () => {
    api.getObservabilitySummary.mockResolvedValue(
      buildSummary({
        queueDepth: 9,
        lastError: 'queue_full',
      }),
    );

    render(<ObservabilityPage />);

    expect(await screen.findByText(/queue depth:\s*9/i)).toBeInTheDocument();
    expect(screen.getByText(/last worker error:\s*queue_full/i)).toBeInTheDocument();
  });

  it('blocks diagnostic search when max priority is not an integer', async () => {
    const user = userEvent.setup();
    render(<ObservabilityPage />);

    const input = await screen.findByLabelText('Max priority filter');
    await user.type(input, '1.9');
    await user.click(screen.getByRole('button', { name: /Run Diagnostic Search/i }));

    expect(api.runObservabilitySearch).not.toHaveBeenCalled();
    expect(await screen.findByText(/max priority must be a non-negative integer/i)).toBeInTheDocument();
  });

  it('sends max priority as an integer filter', async () => {
    const user = userEvent.setup();
    render(<ObservabilityPage />);

    const input = await screen.findByLabelText('Max priority filter');
    await user.type(input, '3');
    await user.click(screen.getByRole('button', { name: /Run Diagnostic Search/i }));

    await waitFor(() => {
      expect(api.runObservabilitySearch).toHaveBeenCalledWith(
        expect.objectContaining({
          filters: expect.objectContaining({ max_priority: 3 }),
        }),
      );
    });
  });

  it('shows explicit message when cancel returns 404', async () => {
    const runningJob = {
      job_id: 'job-cancel-missing',
      status: 'running',
      task_type: 'reindex_memory',
      memory_id: 91,
      reason: 'cancel-missing',
    };
    api.getObservabilitySummary
      .mockResolvedValueOnce(buildSummary({ recentJobs: [runningJob], timestamp: '2026-01-01T00:00:00Z' }))
      .mockResolvedValueOnce(buildSummary({ recentJobs: [runningJob], timestamp: '2026-01-01T00:00:01Z' }));
    api.cancelIndexJob.mockRejectedValueOnce({
      message: "job 'job-cancel-missing' not found.",
      response: {
        status: 404,
        data: {
          detail: "job 'job-cancel-missing' not found.",
        },
      },
    });

    const user = userEvent.setup();
    render(<ObservabilityPage />);

    const jobCard = await getJobCardById('job-cancel-missing');
    await user.click(within(jobCard).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => {
      expect(api.cancelIndexJob).toHaveBeenCalledWith('job-cancel-missing', {
        reason: 'observability_console_cancel',
      });
    });
    expect(api.getObservabilitySummary).toHaveBeenCalledTimes(2);
    expect(await screen.findByText(/Cancel skipped \(job-cancel-missing\): job not found/i)).toBeInTheDocument();
  });

  it('shows explicit message when cancel returns 409', async () => {
    const runningJob = {
      job_id: 'job-cancel-finalized',
      status: 'running',
      task_type: 'reindex_memory',
      memory_id: 92,
      reason: 'cancel-finalized',
    };
    api.getObservabilitySummary
      .mockResolvedValueOnce(buildSummary({ recentJobs: [runningJob], timestamp: '2026-01-01T00:00:00Z' }))
      .mockResolvedValueOnce(buildSummary({ recentJobs: [runningJob], timestamp: '2026-01-01T00:00:01Z' }));
    api.cancelIndexJob.mockRejectedValueOnce({
      message: 'job_already_finalized',
      response: {
        status: 409,
        data: {
          detail: 'job_already_finalized',
        },
      },
    });

    const user = userEvent.setup();
    render(<ObservabilityPage />);

    const jobCard = await getJobCardById('job-cancel-finalized');
    await user.click(within(jobCard).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => {
      expect(api.cancelIndexJob).toHaveBeenCalledWith('job-cancel-finalized', {
        reason: 'observability_console_cancel',
      });
    });
    expect(api.getObservabilitySummary).toHaveBeenCalledTimes(2);
    expect(await screen.findByText(/Cancel skipped \(job-cancel-finalized\): already finalized/i)).toBeInTheDocument();
  });

  it('treats unknown 409 conflicts as cancel failure', async () => {
    const runningJob = {
      job_id: 'job-cancel-conflict',
      status: 'running',
      task_type: 'reindex_memory',
      memory_id: 93,
      reason: 'cancel-conflict',
    };
    api.getObservabilitySummary.mockResolvedValue(buildSummary({ recentJobs: [runningJob] }));
    api.cancelIndexJob.mockRejectedValueOnce({
      message: 'running_job_handle_unavailable',
      response: {
        status: 409,
        data: {
          detail: 'running_job_handle_unavailable',
        },
      },
    });

    const user = userEvent.setup();
    render(<ObservabilityPage />);

    const jobCard = await getJobCardById('job-cancel-conflict');
    await user.click(within(jobCard).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => {
      expect(api.cancelIndexJob).toHaveBeenCalledWith('job-cancel-conflict', {
        reason: 'observability_console_cancel',
      });
    });
    expect(api.getObservabilitySummary).toHaveBeenCalledTimes(1);
    expect(
      await screen.findByText(/Cancel failed \(job-cancel-conflict\): running_job_handle_unavailable/i),
    ).toBeInTheDocument();
  });

  it('switches inspect detail between selected job and active job', async () => {
    const activeJobId = 'job-active';
    const inspectedJobId = 'job-inspect';
    const summary = buildSummary({
      activeJobId,
      recentJobs: [
        {
          job_id: inspectedJobId,
          status: 'failed',
          task_type: 'reindex_memory',
          memory_id: 42,
          reason: 'inspect-target',
        },
      ],
    });
    const jobDetails = {
      [activeJobId]: {
        job_id: activeJobId,
        status: 'running',
        task_type: 'rebuild_index',
        reason: 'active-reason',
      },
      [inspectedJobId]: {
        job_id: inspectedJobId,
        status: 'failed',
        task_type: 'reindex_memory',
        memory_id: 42,
        reason: 'inspect-reason',
      },
    };
    api.getObservabilitySummary.mockResolvedValue(summary);
    api.getIndexJob.mockImplementation(async (jobId) => ({ job: jobDetails[jobId] }));

    const user = userEvent.setup();
    render(<ObservabilityPage />);

    await screen.findByText(/reason:\s*active-reason/i);
    const inspectCard = await getJobCardById(inspectedJobId);
    await user.click(within(inspectCard).getByRole('button', { name: 'Inspect' }));
    await screen.findByText(/reason:\s*inspect-reason/i);

    expect(api.getIndexJob).toHaveBeenCalledWith(inspectedJobId);

    await user.click(screen.getByRole('button', { name: 'Back to Active' }));
    await screen.findByText(/reason:\s*active-reason/i);
    expect(api.getIndexJob).toHaveBeenLastCalledWith(activeJobId);
  });
});
