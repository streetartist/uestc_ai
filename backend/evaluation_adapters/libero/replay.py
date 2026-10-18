"""Stream the two public camera views to an H.264 replay without retaining frames."""
import base64
import io
import json
import subprocess
import time
from PIL import Image


class ReplayWriter:
    def __init__(self, directory):
        self.directory = directory
        self.process = None
        self.log = None
        self.frames = 0
        self.error = None
        self.size = None
        self.source = 'public agent observation'
        self.agent_input_pixels = [128, 128]

    def append(self, sample):
        if self.error:
            return
        try:
            views = [Image.open(io.BytesIO(base64.b64decode(sample['images_jpeg_base64'][name]))).convert('RGB')
                     for name in ('agentview', 'robot0_eye_in_hand')]
            self.agent_input_pixels = list(views[0].size)
            self._append_views(views)
        except (OSError, ValueError, KeyError) as error:
            self._failed(error)

    def append_observation(self, observation):
        """Record original rendered camera pixels before the policy's 128px resize."""
        if self.error:
            return
        try:
            import numpy as np
            views = [Image.fromarray(np.asarray(observation[name+'_image'],dtype=np.uint8)[::-1]).convert('RGB')
                     for name in ('agentview', 'robot0_eye_in_hand')]
            self.source = 'camera replay; policy uses independent frozen 128x128 observations'
            self.agent_input_pixels = [128,128]
            self._append_views(views)
        except (OSError, ValueError, KeyError) as error:
            self._failed(error)

    def _append_views(self, views):
        try:
            if views[0].size != views[1].size:
                raise ValueError('camera dimensions differ')
            width, height = views[0].size
            canvas = Image.new('RGB',(width*2,height))
            canvas.paste(views[0],(0,0)); canvas.paste(views[1],(width,0))
            if self.process is None:
                self.size = canvas.size
                self.log = (self.directory/'replay-encoder.log').open('wb')
                self.process = subprocess.Popen(['ffmpeg','-hide_banner','-loglevel','error','-y',
                    '-f','rawvideo','-pix_fmt','rgb24','-video_size',f'{width*2}x{height}',
                    '-framerate','20','-i','pipe:0','-an','-c:v','libx264','-preset','veryfast',
                    '-crf','24','-pix_fmt','yuv420p','-threads','1','-movflags','+faststart',
                    str(self.directory/'replay.mp4')], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                    stderr=self.log)
            if canvas.size != self.size:
                raise ValueError('camera dimensions changed during episode')
            self.process.stdin.write(canvas.tobytes())
            self.frames += 1
        except (OSError, ValueError, KeyError) as error:
            self._failed(error)

    def _failed(self, error):
        self.error = f'{type(error).__name__}: {error}'
        print(f'LIBERO replay recording failed: {self.error}',flush=True)

    def close(self):
        if self.process is not None:
            try:
                self.process.stdin.close()
                code = self.process.wait(timeout=20)
                if code:
                    self.error = f'ffmpeg exited {code}; see replay-encoder.log'
            except (OSError, subprocess.TimeoutExpired) as error:
                self.error = f'{type(error).__name__}: {error}'
                if self.process.poll() is None:
                    self.process.kill(); self.process.wait()
        if self.log:
            self.log.close()
        output = self.directory/'replay.mp4'
        if self.error:
            print(f'LIBERO replay unavailable: {self.error}',flush=True)
            output.unlink(missing_ok=True)
        state = {'available':bool(not self.error and output.is_file()), 'error':self.error,
                 'frames':self.frames, 'fps':20, 'views':['agentview','robot0_eye_in_hand'],
                 'pixels':self.size, 'source':self.source,
                 'camera_pixels':[self.size[0]//2,self.size[1]] if self.size else None,
                 'agent_input_pixels':self.agent_input_pixels,
                 'alignment':'frame 0 precedes action 1; frame n follows action n'}
        (self.directory/'replay-info.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        return state
