
## Clean cutout: isnet-general-use + alpha matting

> On a studio uniform background this combination gave a clean contour where u2net was unsatisfactory. Model **`isnet-general-use`** (not u2net) + large erode keeps hair and fringe without background halo.

- Model `isnet-general-use` (`~/.u2net/isnet-general-use.onnx`, ~179MB, downloads automatically on first `new_session`).
- `alpha_matting=True`, foreground=240, background=15, **erode_size=12** (large erode = less gray halo at edges).
- Recipe (environment: `~/cloak/bin/python` with rembg/onnxruntime/Pillow):

```python
from rembg import remove, new_session
from PIL import Image
import io
sess = new_session("isnet-general-use")
out = remove(open(src, "rb").read(), session=sess, alpha_matting=True,
             alpha_matting_foreground_threshold=240,
             alpha_matting_background_threshold=15,
             alpha_matting_erode_size=12)
im = Image.open(io.BytesIO(out)).convert("RGBA")
im = im.crop(im.getchannel("A").getbbox())
im.save(dst)
```

- **Hem/arm butting against edge of source photo** (figure in rotation is wider than frame) → cutout inherits a straight vertical slice along the side. Fix: (a) feather along side alpha columns — `a[:, x] *= x/40` for x in 0..40 and mirror on right side, slice dissolves; (b) in poster make figure wider than canvas (1000–1180px on 1080), slice goes beyond edge = reads as intentional crop (exactly like a cutout musician in Tanzu references, "cropped by the frame").
- **Verified** on studio uniform background. On complex frames with instrument (drum/brush) NOT verified — open question on u2net/birefnet remains.
