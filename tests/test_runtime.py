import json
from pathlib import Path
import tempfile
import time
import unittest
from PIL import Image, ImageDraw

from computer_artist.memory import Memory
from computer_artist.runtime import Context, Observations, Interrupted


class Backend:
    def __init__(self):
        self.window={'id':'w','native':True,'visible':True,'agent':False,'pid':1,'title':'Canvas',
                     'x':100,'y':200,'width':100,'height':80}
        self.expires=time.monotonic()+20
        self.budget=1000
        self.failure=None
        self.lease=''
        self.image=Image.new('RGB',(200,160),'white')
        self.actions=[]
        self.acquire_count=0

    def windows(self):
        self.budget-=1
        return [dict(self.window)]

    def request(self,op,**kwargs):
        self.budget-=1
        if op=='capabilities': return {'operations':['move','button','capture','scroll']}
        if op=='capture': self.image.save(kwargs['path']); return {'ok':True}
        raise AssertionError(op)

    def acquire(self,identity):
        self.acquire_count+=1
        self.lease='lease'
        self.window['agent']=True

    def click(self,x,y,button): self.actions.append((x,y,button))
    def move(self,x,y): self.actions.append((x,y))
    def path(self,points,interval): self.actions.extend(points)
    def button(self,pressed=True): self.actions.append(('button',pressed))


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.memory=Memory(self.tmp.name)
        self.backend=Backend()
        self.ctx=Context(self.backend,'w',self.memory)

    def test_scaled_crop_diff_and_observation_retention(self):
        first=self.ctx.observe()
        ImageDraw.Draw(self.backend.image).rectangle((20,20,39,39),fill='black')
        second=self.ctx.observe(since=first['id'])
        self.assertTrue(second['changes']['pixels_changed'])
        self.assertEqual(second['region'],[10,10,10,10])
        with Image.open(second['image']) as im: self.assertEqual(im.size,(20,20))
        self.ctx.observations.limit=2
        self.ctx.observe()
        with self.assertRaises(ValueError): self.ctx.observations.load('w',first['id'])

    def test_named_target_checks_pixels_before_input(self):
        observation=self.ctx.observe()
        self.ctx.target('button',observation=observation['id'],rect=[10,10,20,20])
        self.ctx.click(target='@button')
        self.assertEqual(self.backend.actions,[(120,220,272)])
        ImageDraw.Draw(self.backend.image).rectangle((20,20,30,30),fill='black')
        with self.assertRaises(Interrupted): self.ctx.click(target='@button')
        self.assertEqual(len(self.backend.actions),1)

    def test_geometry_change_prevents_input_and_human_takeover_is_sticky(self):
        self.backend.window['width']=101
        with self.assertRaises(Interrupted): self.ctx.click(relative=(.5,.5))
        self.assertEqual(self.backend.actions,[])
        self.backend.window['width']=100
        with self.assertRaises(Interrupted): self.ctx.click(relative=(.5,.5))

    def test_nested_modules_share_lease_and_budget_and_verify_named_checks(self):
        self.memory.write('w','point','def run(ctx, x: float):\n    ctx.click(relative=(x, .5))\n    return x')
        self.memory.write('w','pair','def run(ctx):\n    a=ctx.memory.call("point",x=.2)\n    b=ctx.memory.call("point",x=.8)\n    return [a,b]\ndef verify(ctx,result):\n    return {"check":"two returned coordinates", "passed":result==[.2,.8], "evidence":result}')
        remaining=self.backend.budget
        self.assertEqual(self.ctx.call('pair',{}),[.2,.8])
        self.assertEqual(self.backend.acquire_count,1)
        self.assertLess(self.backend.budget,remaining)
        self.assertEqual(self.ctx.events[0]['status'],'verified')
        self.assertEqual(self.ctx.events[1]['status'],'returned_unverified')

    def test_recursive_or_unsupported_modules_do_not_dispatch(self):
        self.memory.write('w','keyboard','CONTRACT={"requires":["focus"]}\ndef run(ctx): ctx.click(relative=(.5,.5))')
        with self.assertRaises(ValueError): self.ctx.call('keyboard',{})
        self.memory.write('w','recursive','def run(ctx): ctx.memory.call("recursive")')
        with self.assertRaises(ValueError): self.ctx.call('recursive',{})
        self.assertEqual(self.backend.actions,[])

    def test_wait_branches_and_verification_failure(self):
        observation=self.ctx.observe()
        ImageDraw.Draw(self.backend.image).point((1,1),fill='black')
        result=self.ctx.wait_for({'changed':{'changed_since':observation['id']},'error':{'title_contains':'Error'}},timeout=1)
        self.assertEqual(result['matches'],'changed')
        with self.assertRaises(Interrupted): self.ctx.verify('export exists',False,evidence={'exists':False})
        with self.assertRaises(Interrupted): self.ctx.click(relative=(.5,.5))

    def test_feedback_path_stops_on_condition_and_releases_button(self):
        result=self.ctx.path([(10,10),(20,20),(30,30)], until=lambda observation: True, observe_every=1)
        self.assertEqual(result['status'],'condition_observed')
        self.assertEqual(result['points'],1)
        self.assertEqual(self.backend.actions[-1],('button',False))
        self.assertNotIn((130,230),self.backend.actions)

    def test_host_context_and_host_only_module_require_explicit_lane(self):
        with self.assertRaises(PermissionError): self.ctx.host.window('w')
        self.memory.write('w','host-only','CONTRACT={"lane":"host"}\ndef run(ctx): return 1')
        with self.assertRaises(PermissionError): self.ctx.call('host-only',{})
        with self.assertRaises(PermissionError): self.ctx.focus()
        self.assertEqual(self.backend.acquire_count,0)

    def test_invalid_path_validates_before_any_input(self):
        with self.assertRaises(ValueError): self.ctx.path([(10,10),(1000,1000)])
        self.assertEqual(self.backend.acquire_count,0)
        with self.assertRaises(ValueError): self.ctx.type('hello')
        self.assertEqual(self.backend.acquire_count,0)


if __name__ == '__main__': unittest.main()
