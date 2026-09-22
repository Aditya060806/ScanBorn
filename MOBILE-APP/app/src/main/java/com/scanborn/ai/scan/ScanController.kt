package com.scanborn.ai.scan

import android.app.Activity
import android.content.Context
import android.hardware.display.DisplayManager
import android.opengl.GLES20
import android.opengl.GLSurfaceView
import android.os.Build
import android.util.Log
import android.view.Display
import com.google.ar.core.ArCoreApk
import com.google.ar.core.Camera
import com.google.ar.core.Config
import com.google.ar.core.Frame
import com.google.ar.core.Session
import com.google.ar.core.TrackingFailureReason
import com.google.ar.core.TrackingState
import com.google.ar.core.exceptions.CameraNotAvailableException
import com.google.ar.core.exceptions.NotYetAvailableException
import com.google.ar.core.exceptions.UnavailableException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import org.json.JSONArray
import org.json.JSONObject
import javax.microedition.khronos.egl.EGLConfig
import javax.microedition.khronos.opengles.GL10

/**
 * One scan: ARCore with raw depth, the camera preview, the frame gates, registration on the home
 * marker, and the upload (docs/superpowers/plans/2026-09-15-phase-5-capture.md, decisions 2–9).
 *
 * `resume`, `pause`, `close`, `requestStart` and `finish` come from the UI thread; frames are
 * processed on the GL thread in [onDrawFrame]; uploads run on [UploadQueue]'s thread.
 */
class ScanController(
    private val context: Context,
    private val baseUrl: String,
) : GLSurfaceView.Renderer {

    enum class Phase { CHECKING, INSTALLING, READY, SCANNING, FINISHING, DONE, FAILED }

    data class Status(
        val phase: Phase = Phase.CHECKING,
        val message: String = "Checking this phone for depth…",
        val tracking: String = "",
        val kept: Int = 0,
        val uploaded: Int = 0,
        val pending: Int = 0,
        val rejected: Int = 0,
        val dropped: Int = 0,
        val skipped: Map<String, Int> = emptyMap(),
        val keptShare: Float = 0f,
        val scanId: String? = null,
        val depthMode: String? = null,
        /** A scan saved on this phone that the operator may give up with [discard]. */
        val savedScan: String? = null,
    )

    private val _status = MutableStateFlow(Status())
    val status: StateFlow<Status> = _status

    private val background = BackgroundRenderer()
    @Volatile private var session: Session? = null
    private var depthMode: Config.DepthMode? = null
    private var installRequested = false
    @Volatile private var viewportChanged = false
    private var viewportWidth = 0
    private var viewportHeight = 0
    private var textureBound = false

    @Volatile private var startRequested = false
    @Volatile private var interrupted = false
    private var home: HomeFrame? = null
    private var queue: UploadQueue? = null
    private var frameIndex = 0
    private var lastCaptureNanos = 0L
    private var lastRawTimestamp = -1L
    private var lastQuaternion: FloatArray? = null
    private var lastQuaternionNanos = 0L
    private val skipped = linkedMapOf("tracking" to 0, "turning" to 0, "stale" to 0, "sparse" to 0)
    private val pendingScans = PendingScans(java.io.File(context.filesDir, "pending-scans"))

    init {
        pendingScans.purgeCompleted()
        pendingScans.saved(baseUrl)?.let { saved ->
            interrupted = true
            if (saved.damaged) {
                update { it.copy(phase = Phase.FAILED, savedScan = saved.id,
                    message = "A saved scan's record is damaged, so it cannot be uploaded. Discard it to scan again.") }
            } else {
                attachQueue(saved.id, saved.directory, saved.meta!!)
                update { it.copy(phase = Phase.FAILED, savedScan = saved.id,
                    message = "A saved scan is waiting. Retry upload to finish it, or discard it.") }
            }
        }
    }

    private fun attachQueue(id: String, dir: java.io.File, meta: String) {
        queue = UploadQueue(Uploader(baseUrl, "${Build.MANUFACTURER} ${Build.MODEL}", scanIdentifier = id),
            meta, directory = dir) { counts ->
            update { it.copy(kept = counts.accepted, uploaded = counts.uploaded, pending = counts.pending,
                rejected = counts.rejected, dropped = counts.dropped, scanId = counts.scanId) }
        }
        val counts = queue!!.snapshot()
        frameIndex = counts.accepted + counts.dropped
        update { it.copy(kept = counts.accepted, uploaded = counts.uploaded, pending = counts.pending,
            rejected = counts.rejected, dropped = counts.dropped, scanId = id) }
    }

    // --------------------------------------------------------------------- lifecycle

    /** From the screen's ON_RESUME: install or open ARCore with depth, then resume it. A saved scan waiting
     *  to upload keeps the camera closed until it is finished or discarded. */
    fun resume(activity: Activity) {
        if (interrupted && _status.value.savedScan != null) return
        if (session == null && !open(activity)) return
        try {
            session?.resume()
            textureBound = false
        } catch (e: CameraNotAvailableException) {
            update { it.copy(phase = Phase.FAILED, message = "The camera is in use by another app.") }
            session?.close()
            session = null
        }
    }

    private fun open(activity: Activity): Boolean {
        try {
            val install = ArCoreApk.getInstance().requestInstall(activity, !installRequested)
            if (install == ArCoreApk.InstallStatus.INSTALL_REQUESTED) {
                installRequested = true
                update { it.copy(phase = Phase.INSTALLING, message = "Installing Google Play Services for AR…") }
                return false
            }
            val created = Session(activity)
            val mode = listOf(Config.DepthMode.RAW_DEPTH_ONLY, Config.DepthMode.AUTOMATIC)
                .firstOrNull { created.isDepthModeSupported(it) }
            if (mode == null) {
                created.close()
                update { it.copy(phase = Phase.FAILED, message = NO_DEPTH) }
                return false
            }
            created.configure(created.config.apply {
                depthMode = mode
                focusMode = Config.FocusMode.AUTO
                updateMode = Config.UpdateMode.LATEST_CAMERA_IMAGE
            })
            depthMode = mode
            session = created
            update { it.copy(phase = Phase.READY, message = READY, depthMode = mode.name) }
            return true
        } catch (e: UnavailableException) {
            update {
                it.copy(phase = Phase.FAILED,
                    message = "ARCore is not available (${e.javaClass.simpleName}). $NO_DEPTH")
            }
            return false
        }
    }

    /** From ON_PAUSE. A scan interrupted here keeps what it has: ARCore may re-anchor its world
     *  on resume, so frames after it could not be trusted to share the home frame. */
    @Synchronized fun pause() {
        if (_status.value.phase == Phase.SCANNING) {
            interrupted = true
            update { it.copy(message = "The scan was interrupted. Tap Finish to keep what was captured.") }
        }
        session?.pause()
    }

    /** When the screen goes away (after the GL thread has paused). */
    @Synchronized fun close() {
        queue?.shutdown()
        session?.close()
        session = null
    }

    @Synchronized fun requestStart() {
        startRequested = true
    }

    /** Give up the saved scan (its frames on this phone are deleted), then open the camera so a
     *  new scan can start. Frames the orchestrator already holds stay there. */
    @Synchronized fun discard(activity: Activity) {
        val id = _status.value.savedScan ?: return
        if (_status.value.phase == Phase.FINISHING) return
        queue?.shutdown()
        queue = null
        if (!pendingScans.discard(id)) {
            update { it.copy(phase = Phase.FAILED, scanId = null,
                message = "Could not delete the saved scan; retry Discard or reopen this screen to recover it.") }
            return
        }
        interrupted = false
        home = null
        frameIndex = 0
        synchronized(skipped) { skipped.keys.forEach { skipped[it] = 0 } }
        _status.value = Status()
        resume(activity)
        // Restored scans create a new ARCore session in resume(). A scan discarded in this
        // screen may already own one, so open() will not run to publish READY for us.
        if (_status.value.phase == Phase.CHECKING && session != null) {
            update { it.copy(phase = Phase.READY, message = READY, depthMode = depthMode?.name) }
        }
    }

    /** Upload what is left, then close the scan with its counts. */
    fun finish() = finish(false)

    @Synchronized fun finish(allowPartial: Boolean) {
        val queue = queue ?: return
        if (_status.value.phase !in listOf(Phase.SCANNING, Phase.FAILED)) return
        update { it.copy(phase = Phase.FINISHING, message = "Uploading the last frames…") }
        val skippedNow = synchronized(skipped) { skipped.toMap() }
        val frames = frameIndex
        queue.finish(
            allowPartial = allowPartial,
            finalMeta = { counts ->
                JSONObject()
                    .put("frames", frames)
                    .put("uploaded", counts.uploaded)
                    .put("failed", counts.pending)
                    .put("rejected", counts.rejected)
                    .put("dropped_offline", counts.dropped)
                    .put("skipped", JSONObject(skippedNow))
                    .toString()
            },
        ) { outcome ->
            when (outcome) {
                is UploadQueue.Outcome.Done -> update {
                    pendingScans.purgeCompleted() // the orchestrator holds the frames now
                    it.copy(phase = Phase.DONE, scanId = outcome.scanId, savedScan = null,
                        message = "Scan ${outcome.scanId}: ${outcome.frames} frames on the " +
                            "orchestrator" + lostNote(outcome.counts) +
                            ". Open it in the dashboard's Capture stage.")
                }
                is UploadQueue.Outcome.Failed -> update {
                    it.copy(phase = Phase.FAILED, message = outcome.reason)
                }
            }
        }
    }

    // ------------------------------------------------------------------------- GL

    override fun onSurfaceCreated(gl: GL10?, config: EGLConfig?) {
        GLES20.glClearColor(0f, 0f, 0f, 1f)
        background.createOnGlThread()
        textureBound = false
    }

    override fun onSurfaceChanged(gl: GL10?, width: Int, height: Int) {
        GLES20.glViewport(0, 0, width, height)
        viewportWidth = width
        viewportHeight = height
        viewportChanged = true
    }

    override fun onDrawFrame(gl: GL10?) {
        GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT or GLES20.GL_DEPTH_BUFFER_BIT)
        val session = session ?: return
        if (viewportChanged) {
            session.setDisplayGeometry(displayRotation(), viewportWidth, viewportHeight)
            viewportChanged = false
        }
        if (!textureBound) {
            session.setCameraTextureName(background.textureId)
            textureBound = true
        }
        val frame = try {
            session.update()
        } catch (e: Exception) {
            Log.w(TAG, "session update failed", e)
            return
        }
        background.draw(frame)

        val camera = frame.camera
        val tracking = trackingLabel(camera)
        if (tracking != _status.value.tracking) update { it.copy(tracking = tracking) }
        if (startRequested) {
            startRequested = false
            start(camera)
        }
        if (_status.value.phase == Phase.SCANNING && !interrupted) capture(frame, camera)
    }

    // ------------------------------------------------------------------- scanning

    @Synchronized private fun start(camera: Camera) {
        if (_status.value.phase != Phase.READY) return
        if (camera.trackingState != TrackingState.TRACKING) {
            update { it.copy(message = "Not tracking yet: move the phone slowly, then tap Start again.") }
            return
        }
        val frame = HomeFrame.fromStart(ArFrames.worldFromCamera(camera))
        if (frame == null) {
            update { it.copy(message = "Hold the phone upright, pointing along the marker's arrow, then tap Start.") }
            return
        }
        home = frame
        frameIndex = 0
        lastRawTimestamp = -1L
        lastQuaternion = null
        val id = java.util.UUID.randomUUID().toString()
        val meta = startMeta()
        val dir = try {
            pendingScans.create(id, baseUrl, meta)
        } catch (e: Exception) {
            update { it.copy(message = "Could not save the scan on this phone (${e.message}). Free some space, then tap Start again.") }
            return
        }
        attachQueue(id, dir, meta)
        update { it.copy(phase = Phase.SCANNING, message = SCANNING, savedScan = id) }
    }

    @Synchronized private fun capture(frame: Frame, camera: Camera) {
        if (_status.value.phase != Phase.SCANNING || interrupted) return
        val now = frame.timestamp
        if (now - lastCaptureNanos < CAPTURE_INTERVAL_NANOS) return
        lastCaptureNanos = now
        if (camera.trackingState != TrackingState.TRACKING) {
            lastQuaternion = null
            return skip("tracking")
        }
        val quaternion = ArFrames.quaternion(camera)
        val previous = lastQuaternion
        val previousNanos = lastQuaternionNanos
        lastQuaternion = quaternion
        lastQuaternionNanos = now
        if (previous != null &&
            TurnRate.degreesPerSecond(previous, quaternion, now - previousNanos) > TurnRate.MAX_DEG_PER_S
        ) return skip("turning")
        val home = home ?: return

        try {
            frame.acquireRawDepthImage16Bits().use { depth ->
                if (depth.timestamp == lastRawTimestamp) return skip("stale")
                lastRawTimestamp = depth.timestamp
                frame.acquireRawDepthConfidenceImage().use { confidence ->
                    val d = depth.planes[0]
                    val c = confidence.planes[0]
                    val result = DepthFilter.filter(d.buffer, d.rowStride, d.pixelStride,
                        c.buffer, c.rowStride, c.pixelStride, depth.width, depth.height)
                    if (result.keptShare < MIN_KEPT_SHARE) return skip("sparse")
                    val npz = NpzWriter.frame(
                        depth = result.metres, width = depth.width, height = depth.height,
                        intrinsics = ArFrames.intrinsics(camera, depth.width, depth.height),
                        pose = Frames.cameraToHome(home, ArFrames.worldFromCamera(camera)),
                        color = sampleColor(frame, depth.width, depth.height),
                    )
                    queue?.submit(frameIndex++, npz)
                    update { it.copy(keptShare = result.keptShare) }
                }
            }
        } catch (e: NotYetAvailableException) {
            skip("stale")
        }
    }

    private fun sampleColor(frame: Frame, width: Int, height: Int): ByteArray? = try {
        frame.acquireCameraImage().use { image ->
            val (y, u, v) = image.planes.map { ColorSampler.Plane(it.buffer, it.rowStride, it.pixelStride) }
            ColorSampler.sample(y, u, v, image.width, image.height, width, height)
        }
    } catch (e: NotYetAvailableException) {
        null
    }

    private fun skip(reason: String) {
        val now = synchronized(skipped) {
            skipped[reason] = (skipped[reason] ?: 0) + 1
            skipped.toMap()
        }
        update { it.copy(skipped = now) }
    }

    private fun startMeta(): String = JSONObject()
        .put("device", "${Build.MANUFACTURER} ${Build.MODEL}")
        .put("depth_source", "raw")
        .put("depth_mode", depthMode?.name)
        .put("min_confidence", DepthFilter.MIN_CONFIDENCE)
        .put("range_m", JSONArray().put(DepthFilter.MIN_RANGE_M.toDouble()).put(DepthFilter.MAX_RANGE_M.toDouble()))
        .put("home_pose", JSONArray().put(0.0).put(0.0).put(HOME_HEADING))
        .put("frame", "home: origin at the phone at Start, +Y its forward direction, +Z up")
        .put("capture_hz", 1e9 / CAPTURE_INTERVAL_NANOS)
        .toString()

    private fun lostNote(counts: UploadQueue.Counts): String {
        val lost = counts.pending + counts.rejected + counts.dropped
        return if (lost == 0) "" else " ($lost frames could not be uploaded)"
    }

    private fun trackingLabel(camera: Camera): String = when {
        camera.trackingState == TrackingState.TRACKING -> "tracking"
        camera.trackingFailureReason != TrackingFailureReason.NONE ->
            camera.trackingFailureReason.name.lowercase().replace('_', ' ')
        else -> camera.trackingState.name.lowercase()
    }

    private fun displayRotation(): Int =
        (context.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager)
            .getDisplay(Display.DEFAULT_DISPLAY).rotation

    private fun update(change: (Status) -> Status) = _status.update(change)

    private companion object {
        const val TAG = "ScanController"
        const val CAPTURE_INTERVAL_NANOS = 333_000_000L // ~3 frames a second
        const val MIN_KEPT_SHARE = 0.05f
        const val HOME_HEADING = 1.5708 // π/2 to 4 places: the scan faces +Y at Start
        const val READY = "Stand over the home marker, point the phone along its arrow, and tap Start."
        const val SCANNING = "Walk slowly and sweep the room at chest height. Turns faster than " +
            "45°/s are skipped. Tap Finish when the floor is covered."
        const val NO_DEPTH = "This phone cannot give ARCore depth, so it cannot scan. Scan with " +
            "Scaniverse and import the .ply in the dashboard instead."
    }
}
