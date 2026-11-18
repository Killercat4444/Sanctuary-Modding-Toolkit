"""Shield-only DDS preview adapter. Original payloads/export codecs are untouched.

BC7 requires Blender's bundled OpenImageIO (validated with 3.0.6.1 / Blender 4.5).
Sanctuary's exported DX10 containers omit DDSCAPS_TEXTURE. Normalize that flag
only in an ephemeral preview copy; compressed blocks and exported bytes stay exact.
"""
import struct,tempfile
from pathlib import Path
import numpy as np

def describe(blob):
 if len(blob)<128 or blob[:4]!=b'DDS ':raise ValueError('Shield texture is not a complete DDS')
 if struct.unpack_from('<I',blob,4)[0]!=124 or struct.unpack_from('<I',blob,76)[0]!=32:raise ValueError('Invalid shield DDS structure size')
 height,width=struct.unpack_from('<II',blob,12)
 if struct.unpack_from('<I',blob,8)[0]&0x800000 or struct.unpack_from('<I',blob,112)[0]&0x20fe00:raise ValueError('Shield preview requires a 2D texture, not a cubemap/volume')
 if not 0<width<=4096 or not 0<height<=4096:raise ValueError('Shield preview supports 2D textures up to 4096 pixels per side')
 result={'width':width,'height':height,'fourcc':bytes(blob[84:88]).decode('ascii',errors='replace')}
 if result['fourcc']=='DX10':
  if len(blob)<148:raise ValueError('Truncated shield DX10 header')
  fmt,dimension,misc,array,alpha=struct.unpack_from('<IIIII',blob,128)
  if fmt not in (98,99):raise ValueError('Unsupported shield DXGI format '+str(fmt)+'; BC7 UNORM/SRGB (98/99) supported')
  if dimension!=3 or array!=1 or misc&4:raise ValueError('Shield BC7 preview requires a single 2D texture, not an array/cubemap/volume')
  if (alpha&7) not in (0,1):raise ValueError('Shield BC7 preview requires straight/unspecified alpha')
  if len(blob)<148+((width+3)//4)*((height+3)//4)*16:raise ValueError('Truncated shield BC7 top mip')
  result.update(dxgi_format=fmt,resource_dimension=dimension,array_size=array,alpha_mode=alpha&7)
 elif result['fourcc']=='\x00\x00\x00\x00':
  _,flags,_,bits,*masks=struct.unpack_from('<8I',blob,76)
  if flags!=65 or bits!=32 or masks!=[0xff0000,0xff00,0xff,0xff000000]:raise ValueError('Unsupported uncompressed shield DDS pixel masks')
  pitch=struct.unpack_from('<I',blob,20)[0] if struct.unpack_from('<I',blob,8)[0]&8 else width*4
  if pitch<width*4 or len(blob)<128+pitch*height:raise ValueError('Truncated uncompressed shield DDS')
  result.update(fourcc='BGRA8',pitch=pitch)
 elif result['fourcc'] not in ('DXT1','DXT5'):raise ValueError('Unsupported shield DDS encoding '+result['fourcc'])
 return result

def decode(blob):
 info=describe(blob)
 if info['fourcc']=='BGRA8':
  pixels=np.frombuffer(blob,dtype=np.uint8,count=info['pitch']*info['height'],offset=128).reshape(info['height'],info['pitch'])[:,:info['width']*4].reshape(info['height'],info['width'],4)
  return pixels[:,:,[2,1,0,3]].copy()
 if info['fourcc']!='DX10':
  from .dds_tools import decode_dds_top
  return decode_dds_top(blob)
 try:import OpenImageIO as oiio
 except ImportError as exc:raise ValueError('BC7 shield preview needs the OpenImageIO supplied with Blender 4.5; no external decoder is installed automatically') from exc
 preview=bytearray(blob)
 caps=struct.unpack_from('<I',preview,108)[0]|0x1000
 mipmaps=struct.unpack_from('<I',preview,28)[0]
 if mipmaps>1:caps|=0x400008
 struct.pack_into('<I',preview,108,caps)
 with tempfile.TemporaryDirectory(prefix='sanctuary-shield-dds-') as directory:
  path=Path(directory)/'preview.dds';path.write_bytes(preview)
  handle=oiio.ImageInput.open(str(path))
  if handle is None:raise ValueError('BC7 shield decode failed: '+oiio.geterror())
  try:
   spec=handle.spec()
   if (spec.width,spec.height,spec.nchannels)!=(info['width'],info['height'],4):raise ValueError('Unexpected BC7 shield image shape')
   rgba=handle.read_image(oiio.UINT8)
   if rgba is None:raise ValueError('BC7 pixel read failed: '+handle.geterror())
   rgba=np.asarray(rgba,dtype=np.uint8).copy()
   if rgba.shape!=(info['height'],info['width'],4):raise ValueError('Invalid BC7 pixel layout')
   return rgba
  finally:handle.close()
